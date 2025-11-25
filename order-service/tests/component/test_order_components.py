"""
Component tests for order-service

These tests verify the interaction between multiple components:
- API endpoints (FastAPI routes)
- Service layer (business logic)
- Repository layer (data storage)
- External HTTP client (car-service integration)

Component tests use mocked external dependencies (httpx for car-service calls)
but test real interactions between internal service components.
"""
import pytest
from unittest.mock import patch, AsyncMock, Mock
from fastapi.testclient import TestClient
from datetime import datetime
from uuid import uuid4, UUID
import httpx

from app.main import app
from app.repositories.local_order_repo import LocalOrderRepository
from app.services.order_service import OrderService
from app.services.car_client import CarServiceClient


# Mock user_id for authentication in tests
TEST_USER_ID = uuid4()


def mock_get_current_user_id():
    """Mock authentication dependency that returns a test user ID"""
    return TEST_USER_ID


@pytest.fixture
def test_client():
    """
    Fixture providing FastAPI TestClient with mocked authentication.
    All API endpoints require authentication, so we override the auth dependency.
    """
    from app.auth import get_current_user_id

    app.dependency_overrides[get_current_user_id] = mock_get_current_user_id

    client = TestClient(app)
    yield client

    # Clean up dependency overrides
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_repository():
    """
    Fixture providing a clean repository for each test.
    Auto-used fixture that clears the singleton repository before and after each test.
    Ensures test isolation by clearing all data.
    """
    # Import the singleton repository
    from app.repositories.local_order_repo import order_repository

    # Clean before test
    order_repository._orders.clear()
    order_repository._reviews.clear()
    order_repository._order_reviews.clear()

    yield order_repository

    # Clean up after test
    order_repository._orders.clear()
    order_repository._reviews.clear()
    order_repository._order_reviews.clear()


@pytest.fixture
def test_order_data():
    """Fixture providing sample order creation data"""
    return {
        "car_id": str(uuid4()),
        "desired_time": datetime(2025, 12, 20, 10, 0, 0).isoformat(),
        "description": "Replace brake pads and check suspension"
    }


@pytest.fixture
def test_review_data():
    """Fixture providing sample review data"""
    return {
        "rating": 5,
        "comment": "Excellent service, highly professional mechanics!"
    }


class TestOrderCreationWithCarServiceIntegration:
    """
    Component tests for order creation with car-service HTTP integration.

    Tests the interaction between:
    - API endpoint (POST /api/orders)
    - OrderService business logic
    - CarServiceClient HTTP client
    - LocalOrderRepository data storage
    """

    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_create_order_with_car_service_success(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data
    ):
        """
        Test successful order creation with car-service verification.

        Scenario:
        1. Client sends POST request to create order
        2. OrderService calls CarServiceClient to verify car exists
        3. CarServiceClient returns True (car found)
        4. OrderService creates order via repository
        5. API returns 201 with order details
        """
        # ARRANGE: Mock car-service HTTP call to return success
        mock_verify_car.return_value = True

        # ACT: Create order
        response = test_client.post(
            "/api/orders",
            json=test_order_data
        )

        # ASSERT: Verify complete workflow
        assert response.status_code == 201

        # Verify response structure
        data = response.json()
        assert "order_id" in data
        assert data["car_id"] == test_order_data["car_id"]
        assert data["status"] == "created"
        assert data["description"] == test_order_data["description"]
        assert "appointment_time" in data
        assert "created_at" in data

        # Verify car-service was called with correct car_id
        mock_verify_car.assert_called_once_with(test_order_data["car_id"])

        # Verify order was stored in repository
        order_id = UUID(data["order_id"])
        stored_order = clean_repository._orders.get(order_id)
        assert stored_order is not None
        assert stored_order.status == "created"


    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_create_order_car_service_returns_404(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data
    ):
        """
        Test order creation fails when car-service returns 404.

        Scenario:
        1. Client sends POST request to create order
        2. OrderService calls CarServiceClient to verify car exists
        3. CarServiceClient returns False (car not found - 404)
        4. OrderService raises HTTPException(404)
        5. API returns 404 error without creating order
        """
        # ARRANGE: Mock car-service to return False (car not found)
        mock_verify_car.return_value = False

        # ACT: Attempt to create order with non-existent car
        response = test_client.post(
            "/api/orders",
            json=test_order_data
        )

        # ASSERT: Verify error handling
        assert response.status_code == 404

        data = response.json()
        assert "detail" in data
        assert "Car not found" in data["detail"]

        # Verify car-service was called
        mock_verify_car.assert_called_once_with(test_order_data["car_id"])

        # Verify no order was created in repository
        assert len(clean_repository._orders) == 0


    @patch('httpx.AsyncClient.get')
    def test_create_order_car_service_timeout(
        self,
        mock_httpx_get,
        test_client,
        clean_repository,
        test_order_data
    ):
        """
        Test order creation handles car-service timeout gracefully.

        Scenario:
        1. Client sends POST request to create order
        2. OrderService calls CarServiceClient
        3. HTTP request to car-service times out
        4. CarServiceClient catches TimeoutException and returns False
        5. OrderService treats it as car not found and returns 404
        """
        # ARRANGE: Mock httpx to raise TimeoutException
        mock_httpx_get.side_effect = httpx.TimeoutException("Connection timeout")

        # ACT: Attempt to create order with car-service unavailable
        response = test_client.post(
            "/api/orders",
            json=test_order_data
        )

        # ASSERT: Verify error handling for network issues
        assert response.status_code == 404

        data = response.json()
        assert "Car not found" in data["detail"]

        # Verify no order was created
        assert len(clean_repository._orders) == 0


class TestOrderStatusTransitionValidation:
    """
    Component tests for order status updates with validation logic.

    Tests the interaction between:
    - API endpoint (PATCH /api/orders/{order_id}/status)
    - OrderService status transition validation
    - LocalOrderRepository state management
    """

    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_valid_status_transitions_sequence(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data
    ):
        """
        Test complete valid status transition workflow.

        Scenario:
        1. Create order (status: created)
        2. Update: created -> in_progress (VALID)
        3. Update: in_progress -> work_completed (VALID)
        4. Update: work_completed -> car_issued (VALID)

        Verifies OrderService validates each transition against STATUS_TRANSITIONS map.
        """
        # ARRANGE: Mock car-service and use clean repository
        mock_verify_car.return_value = True

        # Create initial order
        create_response = test_client.post("/api/orders", json=test_order_data)
        assert create_response.status_code == 201
        order_id = create_response.json()["order_id"]

        # ACT & ASSERT: Test each valid transition

        # Transition 1: created -> in_progress
        response1 = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "in_progress"}
        )
        assert response1.status_code == 200
        assert response1.json()["status"] == "in_progress"

        # Verify repository state updated
        order = clean_repository._orders[UUID(order_id)]
        assert order.status == "in_progress"

        # Transition 2: in_progress -> work_completed
        response2 = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "work_completed"}
        )
        assert response2.status_code == 200
        assert response2.json()["status"] == "work_completed"

        # Transition 3: work_completed -> car_issued
        response3 = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "car_issued"}
        )
        assert response3.status_code == 200
        assert response3.json()["status"] == "car_issued"

        # Verify final repository state
        final_order = clean_repository._orders[UUID(order_id)]
        assert final_order.status == "car_issued"


    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_invalid_status_transition_skipping_steps(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data
    ):
        """
        Test invalid status transition is rejected.

        Scenario:
        1. Create order (status: created)
        2. Attempt: created -> work_completed (INVALID - skips in_progress)
        3. OrderService validates against STATUS_TRANSITIONS
        4. Returns 400 with error message listing valid transitions
        """
        # ARRANGE: Create order with initial status
        mock_verify_car.return_value = True

        create_response = test_client.post("/api/orders", json=test_order_data)
        order_id = create_response.json()["order_id"]

        # ACT: Attempt invalid transition
        response = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "work_completed"}  # Invalid from 'created'
        )

        # ASSERT: Verify validation error
        assert response.status_code == 400

        data = response.json()
        assert "Invalid status transition" in data["detail"]
        assert "created" in data["detail"]
        assert "work_completed" in data["detail"]

        # Verify order status unchanged in repository
        order = clean_repository._orders[UUID(order_id)]
        assert order.status == "created"


    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_terminal_state_car_issued_rejects_updates(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data
    ):
        """
        Test that terminal state 'car_issued' cannot be changed.

        Scenario:
        1. Create order and transition through all valid states to car_issued
        2. Attempt to change status from car_issued to any other status
        3. OrderService validates and rejects (no valid transitions from car_issued)
        4. Returns 400 error
        """
        # ARRANGE: Create order and move to terminal state
        mock_verify_car.return_value = True

        create_response = test_client.post("/api/orders", json=test_order_data)
        order_id = create_response.json()["order_id"]

        # Move through valid transitions to terminal state
        test_client.patch(f"/api/orders/{order_id}/status", json={"status": "in_progress"})
        test_client.patch(f"/api/orders/{order_id}/status", json={"status": "work_completed"})
        test_client.patch(f"/api/orders/{order_id}/status", json={"status": "car_issued"})

        # ACT: Attempt to change terminal state
        response = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "in_progress"}
        )

        # ASSERT: Verify rejection
        assert response.status_code == 400
        assert "Invalid status transition" in response.json()["detail"]

        # Verify status remains car_issued
        order = clean_repository._orders[UUID(order_id)]
        assert order.status == "car_issued"


class TestReviewManagement:
    """
    Component tests for review creation and validation.

    Tests the interaction between:
    - API endpoint (POST /api/orders/review)
    - OrderService review validation logic
    - LocalOrderRepository review storage
    """

    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_add_review_to_existing_order(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data,
        test_review_data
    ):
        """
        Test successfully adding a review to an order.

        Scenario:
        1. Create order
        2. Add review via POST /api/orders/review
        3. OrderService verifies order exists
        4. OrderService checks no existing review
        5. Repository creates review and links to order
        6. Returns 201 with review details
        """
        # ARRANGE: Create an order first
        mock_verify_car.return_value = True

        create_response = test_client.post("/api/orders", json=test_order_data)
        order_id = create_response.json()["order_id"]

        # ACT: Add review to order
        review_response = test_client.post(
            f"/api/orders/review?order_id={order_id}",
            json=test_review_data
        )

        # ASSERT: Verify review creation
        assert review_response.status_code == 201

        data = review_response.json()
        assert "review_id" in data
        assert data["order_id"] == order_id
        assert data["status"] == "published"
        assert data["rating"] == test_review_data["rating"]
        assert data["comment"] == test_review_data["comment"]
        assert "created_at" in data

        # Verify review stored in repository
        review_id = UUID(data["review_id"])
        assert review_id in clean_repository._reviews

        # Verify order-review mapping
        assert UUID(order_id) in clean_repository._order_reviews
        assert clean_repository._order_reviews[UUID(order_id)] == review_id


    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_prevent_duplicate_reviews(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data,
        test_review_data
    ):
        """
        Test that duplicate reviews are rejected with 409 Conflict.

        Scenario:
        1. Create order
        2. Add first review (succeeds)
        3. Attempt to add second review to same order
        4. OrderService checks has_review() via repository
        5. Returns 409 Conflict error
        """
        # ARRANGE: Create order and add first review
        mock_verify_car.return_value = True

        create_response = test_client.post("/api/orders", json=test_order_data)
        order_id = create_response.json()["order_id"]

        # Add first review
        first_review = test_client.post(
            f"/api/orders/review?order_id={order_id}",
            json=test_review_data
        )
        assert first_review.status_code == 201

        # ACT: Attempt to add duplicate review
        duplicate_review = test_client.post(
            f"/api/orders/review?order_id={order_id}",
            json={"rating": 3, "comment": "Different comment"}
        )

        # ASSERT: Verify rejection
        assert duplicate_review.status_code == 409

        data = duplicate_review.json()
        assert "Review for this order already exists" in data["detail"]

        # Verify only one review exists in repository
        assert len(clean_repository._reviews) == 1


    def test_add_review_order_not_found(
        self,
        test_client,
        test_review_data
    ):
        """
        Test adding review to non-existent order returns 404.

        Scenario:
        1. Attempt to add review to random UUID (order doesn't exist)
        2. OrderService calls repository.get_order_by_id()
        3. Repository returns None
        4. OrderService raises HTTPException(404)
        """
        # ARRANGE: Generate random order ID that doesn't exist
        non_existent_order_id = str(uuid4())

        # ACT: Attempt to add review
        response = test_client.post(
            f"/api/orders/review?order_id={non_existent_order_id}",
            json=test_review_data
        )

        # ASSERT: Verify 404 error
        assert response.status_code == 404

        data = response.json()
        assert "Order not found" in data["detail"]


class TestCompleteOrderLifecycle:
    """
    End-to-end component test for complete order lifecycle.

    This test verifies the interaction of all components in a realistic workflow:
    - Order creation with car-service verification
    - Multiple status transitions with validation
    - Review addition

    This is a comprehensive integration test that ensures all components
    work together correctly in a real-world scenario.
    """

    @patch('app.services.car_client.car_client.verify_car_exists')
    def test_complete_order_workflow_from_creation_to_review(
        self,
        mock_verify_car,
        test_client,
        clean_repository,
        test_order_data,
        test_review_data
    ):
        """
        Test complete order lifecycle with all component interactions.

        Comprehensive workflow:
        1. Create order (HTTP call to car-service)
        2. Update status: created -> in_progress
        3. Update status: in_progress -> work_completed
        4. Add customer review
        5. Update status: work_completed -> car_issued

        Verifies:
        - Car-service HTTP integration
        - Status transition validation
        - Review creation and duplicate prevention
        - Repository state management
        - API response correctness at each step
        """
        # ARRANGE: Mock car-service HTTP client
        mock_verify_car.return_value = True

        # STEP 1: Create order with car verification
        create_response = test_client.post("/api/orders", json=test_order_data)

        # Verify order creation
        assert create_response.status_code == 201
        order_data = create_response.json()
        order_id = order_data["order_id"]
        assert order_data["status"] == "created"
        assert order_data["car_id"] == test_order_data["car_id"]

        # Verify car-service was called
        mock_verify_car.assert_called_once_with(test_order_data["car_id"])

        # Verify repository state
        assert len(clean_repository._orders) == 1
        stored_order = clean_repository._orders[UUID(order_id)]
        assert stored_order.status == "created"

        # STEP 2: Start work (created -> in_progress)
        update1_response = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "in_progress"}
        )

        assert update1_response.status_code == 200
        assert update1_response.json()["status"] == "in_progress"
        assert stored_order.status == "in_progress"

        # STEP 3: Complete work (in_progress -> work_completed)
        update2_response = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "work_completed"}
        )

        assert update2_response.status_code == 200
        assert update2_response.json()["status"] == "work_completed"
        assert stored_order.status == "work_completed"

        # STEP 4: Add customer review
        review_response = test_client.post(
            f"/api/orders/review?order_id={order_id}",
            json=test_review_data
        )

        assert review_response.status_code == 201
        review_data = review_response.json()
        assert review_data["order_id"] == order_id
        assert review_data["rating"] == test_review_data["rating"]
        assert review_data["status"] == "published"

        # Verify review stored and linked
        review_id = UUID(review_data["review_id"])
        assert review_id in clean_repository._reviews
        assert clean_repository._order_reviews[UUID(order_id)] == review_id

        # STEP 5: Issue car to customer (work_completed -> car_issued)
        update3_response = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "car_issued"}
        )

        assert update3_response.status_code == 200
        final_data = update3_response.json()
        assert final_data["status"] == "car_issued"
        assert stored_order.status == "car_issued"

        # FINAL VERIFICATION: Ensure repository state is consistent
        assert len(clean_repository._orders) == 1
        assert len(clean_repository._reviews) == 1
        assert len(clean_repository._order_reviews) == 1

        # Verify terminal state cannot be changed
        invalid_transition = test_client.patch(
            f"/api/orders/{order_id}/status",
            json={"status": "in_progress"}
        )
        assert invalid_transition.status_code == 400
        assert stored_order.status == "car_issued"  # Status unchanged
