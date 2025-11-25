"""Component tests for payment service.

Component tests verify the interaction between multiple components:
- API endpoints -> Service layer -> Repository layer
- Service layer -> RabbitMQ publisher
- Async payment processing workflow
- End-to-end payment lifecycle
"""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.local_payment_repo import PaymentRepository
from app.services.payment_service import PaymentService
from app.services.rabbitmq_publisher import RabbitMQPublisher


class TestPaymentCreationComponent:
    """Component tests for payment creation workflow."""

    def test_create_payment_with_pending_status(self, test_client: TestClient):
        """
        Test creating a payment returns pending status and stores correctly.

        This test verifies:
        1. API endpoint receives request
        2. Service layer creates payment
        3. Repository stores payment data
        4. Response contains correct status and structure
        """
        # Arrange
        payload = {
            "order_id": "ord_component_test_001",
            "payment_method": "card"
        }

        with patch("app.services.payment_service.rabbitmq_publisher") as mock_publisher:
            mock_publisher.publish_payment_success = AsyncMock()

            # Act
            response = test_client.post("/api/payments", json=payload)

            # Assert - API response
            assert response.status_code == 201
            data = response.json()
            assert data["order_id"] == "ord_component_test_001"
            assert data["status"] == "pending"
            assert data["amount"] == 5000.00
            assert data["currency"] == "RUB"
            assert data["payment_id"].startswith("pay_")
            assert "confirmation_url" in data
            assert "payment.gateway/confirm" in data["confirmation_url"]

            # Assert - Repository storage
            from app.repositories import payment_repository
            stored_payment = payment_repository.get_payment_by_id(data["payment_id"])
            assert stored_payment is not None
            assert stored_payment["status"] == "pending"
            assert stored_payment["order_id"] == "ord_component_test_001"
            assert stored_payment["payment_method"] == "card"
            assert stored_payment["paid_at"] is None


class TestPaymentStatusRetrievalComponent:
    """Component tests for payment status retrieval."""

    def test_get_payment_status_flow(self, test_client: TestClient):
        """
        Test retrieving payment status through complete component stack.

        This test verifies:
        1. Payment is created and stored
        2. Status endpoint retrieves correct data
        3. Repository lookup works correctly
        4. Response structure is correct
        """
        # Arrange - Create payment first
        create_payload = {
            "order_id": "ord_status_test_001",
            "payment_method": "card"
        }

        with patch("app.services.payment_service.rabbitmq_publisher") as mock_publisher:
            mock_publisher.publish_payment_success = AsyncMock()

            # Create payment
            create_response = test_client.post("/api/payments", json=create_payload)
            assert create_response.status_code == 201
            payment_id = create_response.json()["payment_id"]

            # Act - Get payment status
            status_response = test_client.get(f"/api/payments/{payment_id}")

            # Assert - Status response
            assert status_response.status_code == 200
            status_data = status_response.json()
            assert status_data["payment_id"] == payment_id
            assert status_data["status"] == "pending"
            assert status_data["paid_at"] is None

            # Assert - Verify repository has correct data
            from app.repositories import payment_repository
            stored_payment = payment_repository.get_payment_by_id(payment_id)
            assert stored_payment is not None
            assert stored_payment["status"] == "pending"


class TestRabbitMQPublishingComponent:
    """Component tests for RabbitMQ event publishing."""

    @pytest.mark.asyncio
    async def test_payment_success_event_publishing(self, payment_repository: PaymentRepository):
        """
        Test that successful payment publishes event to RabbitMQ.

        This test verifies:
        1. Payment service processes payment asynchronously
        2. Status is updated to 'succeeded'
        3. RabbitMQ publisher is called with correct parameters
        4. Message format is correct
        """
        # Arrange
        payment_service = PaymentService()
        payment_service.repository = payment_repository

        order_id = "ord_rabbitmq_test_001"
        user_id = str(uuid4())
        amount = 5000.00

        # Mock RabbitMQ publisher
        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock()

        # Act - Create payment with mocked publisher
        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            payment = await payment_service.initiate_payment(
                order_id=order_id,
                payment_method="card",
                user_id=user_id,
                amount=amount
            )

            payment_id = payment["payment_id"]

            # Wait for async processing to complete (5 second delay + buffer)
            await asyncio.sleep(5.5)

            # Assert - Payment status updated
            updated_payment = payment_repository.get_payment_by_id(payment_id)
            assert updated_payment is not None
            assert updated_payment["status"] == "succeeded"
            assert updated_payment["paid_at"] is not None

            # Assert - RabbitMQ publisher was called
            mock_publisher.publish_payment_success.assert_called_once_with(
                order_id=order_id,
                user_id=user_id,
                amount=amount
            )


class TestAsyncPaymentProcessingComponent:
    """Component tests for asynchronous payment processing."""

    @pytest.mark.asyncio
    async def test_payment_status_changes_to_succeeded(self, payment_repository: PaymentRepository):
        """
        Test payment status changes from pending to succeeded after processing.

        This test verifies:
        1. Payment starts with 'pending' status
        2. Async task processes payment
        3. Status changes to 'succeeded'
        4. paid_at timestamp is set
        """
        # Arrange
        payment_service = PaymentService()
        payment_service.repository = payment_repository

        order_id = "ord_async_test_001"
        user_id = str(uuid4())

        # Mock RabbitMQ publisher
        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock()

        # Act
        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            # Create payment
            payment = await payment_service.initiate_payment(
                order_id=order_id,
                payment_method="card",
                user_id=user_id,
                amount=4500.00
            )

            payment_id = payment["payment_id"]

            # Assert - Initial status is pending
            initial_payment = payment_repository.get_payment_by_id(payment_id)
            assert initial_payment["status"] == "pending"
            assert initial_payment["paid_at"] is None

            # Wait for async processing
            await asyncio.sleep(5.5)

            # Assert - Final status is succeeded
            final_payment = payment_repository.get_payment_by_id(payment_id)
            assert final_payment["status"] == "succeeded"
            assert final_payment["paid_at"] is not None
            assert isinstance(final_payment["paid_at"], datetime)


class TestCompletePaymentWorkflowComponent:
    """Component tests for complete payment workflow scenarios."""

    def test_end_to_end_payment_workflow_sync(self, test_client: TestClient):
        """
        Test complete payment workflow: creation -> status check -> manual update -> verification.

        This test verifies:
        1. Payment is created via API with 'pending' status
        2. Payment can be retrieved with 'pending' status
        3. Status update works correctly
        4. Updated status can be retrieved via API

        Note: This is a synchronous test that manually simulates payment processing
        rather than waiting for async tasks, as TestClient doesn't support background tasks.
        """
        # Arrange
        order_id = "ord_e2e_test_001"
        payload = {
            "order_id": order_id,
            "payment_method": "card"
        }

        # Mock RabbitMQ publisher
        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock()

        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            # Act 1 - Create payment
            create_response = test_client.post("/api/payments", json=payload)

            # Assert 1 - Payment created successfully
            assert create_response.status_code == 201
            payment_data = create_response.json()
            payment_id = payment_data["payment_id"]
            assert payment_data["status"] == "pending"
            assert payment_data["order_id"] == order_id

            # Act 2 - Check initial status
            status_response_1 = test_client.get(f"/api/payments/{payment_id}")

            # Assert 2 - Initial status is pending
            assert status_response_1.status_code == 200
            status_data_1 = status_response_1.json()
            assert status_data_1["status"] == "pending"
            assert status_data_1["paid_at"] is None

            # Act 3 - Manually simulate payment processing completion
            from app.repositories import payment_repository
            from datetime import datetime
            payment_repository.update_payment_status(
                payment_id, "succeeded", datetime.utcnow()
            )

            # Act 4 - Check final status
            status_response_2 = test_client.get(f"/api/payments/{payment_id}")

            # Assert 3 - Final status is succeeded
            assert status_response_2.status_code == 200
            status_data_2 = status_response_2.json()
            assert status_data_2["status"] == "succeeded"
            assert status_data_2["paid_at"] is not None

    def test_payment_workflow_with_duplicate_prevention(self, test_client: TestClient):
        """
        Test payment workflow prevents duplicate payments for same order.

        This test verifies:
        1. First payment is created successfully
        2. Payment is manually updated to 'succeeded'
        3. Attempt to create second payment for same order fails
        4. Error response is appropriate (409 Conflict)
        """
        # Arrange
        order_id = "ord_duplicate_test_001"
        payload = {
            "order_id": order_id,
            "payment_method": "card"
        }

        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock()

        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            # Act 1 - Create first payment
            response_1 = test_client.post("/api/payments", json=payload)
            assert response_1.status_code == 201
            payment_id = response_1.json()["payment_id"]

            # Manually update payment to succeeded
            from app.repositories import payment_repository
            from datetime import datetime
            payment_repository.update_payment_status(
                payment_id, "succeeded", datetime.utcnow()
            )

            # Verify payment succeeded
            payment = payment_repository.get_payment_by_id(payment_id)
            assert payment["status"] == "succeeded"

            # Act 2 - Attempt to create duplicate payment
            response_2 = test_client.post("/api/payments", json=payload)

            # Assert - Duplicate rejected
            assert response_2.status_code == 409
            error_data = response_2.json()
            assert "already paid" in error_data["detail"].lower()
            assert order_id in error_data["detail"]

    def test_multiple_payments_different_orders_workflow(self, test_client: TestClient):
        """
        Test workflow with multiple payments for different orders.

        This test verifies:
        1. Multiple payments can be created sequentially
        2. Each payment has unique ID
        3. All payments can be tracked independently
        4. Repository stores all payments correctly
        """
        # Arrange
        orders = [
            {"order_id": "ord_multi_001", "payment_method": "card"},
            {"order_id": "ord_multi_002", "payment_method": "sbp"},
            {"order_id": "ord_multi_003", "payment_method": "card"}
        ]

        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock()

        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            payment_ids = []

            # Act 1 - Create multiple payments
            for order in orders:
                response = test_client.post("/api/payments", json=order)
                assert response.status_code == 201
                payment_ids.append(response.json()["payment_id"])

            # Assert 1 - All payment IDs are unique
            assert len(payment_ids) == len(set(payment_ids))

            # Assert 2 - All payments are stored and retrievable
            from app.repositories import payment_repository
            for i, payment_id in enumerate(payment_ids):
                payment = payment_repository.get_payment_by_id(payment_id)
                assert payment is not None
                assert payment["status"] == "pending"
                assert payment["order_id"] == orders[i]["order_id"]
                assert payment["payment_method"] == orders[i]["payment_method"]


class TestPaymentComponentErrorHandling:
    """Component tests for error handling across components."""

    @pytest.mark.asyncio
    async def test_payment_processing_failure_handling(self, payment_repository: PaymentRepository):
        """
        Test error handling when RabbitMQ publishing fails.

        This test verifies:
        1. Payment is created successfully
        2. If RabbitMQ publishing fails, payment status is handled gracefully
        3. Error is logged but doesn't crash the service
        """
        # Arrange
        payment_service = PaymentService()
        payment_service.repository = payment_repository

        order_id = "ord_error_test_001"
        user_id = str(uuid4())

        # Mock RabbitMQ publisher to raise exception
        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock(
            side_effect=Exception("RabbitMQ connection error")
        )

        # Act
        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            payment = await payment_service.initiate_payment(
                order_id=order_id,
                payment_method="card",
                user_id=user_id,
                amount=3000.00
            )

            payment_id = payment["payment_id"]

            # Wait for async processing
            await asyncio.sleep(5.5)

            # Assert - Payment status is updated to failed
            final_payment = payment_repository.get_payment_by_id(payment_id)
            assert final_payment is not None
            assert final_payment["status"] == "failed"

    def test_payment_not_found_component_flow(self, test_client: TestClient):
        """
        Test error flow when requesting non-existent payment.

        This test verifies:
        1. API endpoint handles missing payment correctly
        2. Service layer returns None
        3. Endpoint converts to proper HTTP 404 error
        """
        # Act
        response = test_client.get("/api/payments/pay_nonexistent_xyz")

        # Assert
        assert response.status_code == 404
        error_data = response.json()
        assert "not found" in error_data["detail"].lower()
        assert "pay_nonexistent_xyz" in error_data["detail"]


class TestPaymentComponentIntegration:
    """Component tests for service integration points."""

    @pytest.mark.asyncio
    async def test_repository_service_integration(self, payment_repository: PaymentRepository):
        """
        Test integration between repository and service layers.

        This test verifies:
        1. Service can create payments via repository
        2. Service can retrieve payments from repository
        3. Service can update payment status via repository
        4. Data consistency is maintained
        """
        # Arrange
        payment_service = PaymentService()
        payment_service.repository = payment_repository

        order_id = "ord_integration_001"
        user_id = str(uuid4())

        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock()

        # Act
        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            # Create payment
            created_payment = await payment_service.initiate_payment(
                order_id=order_id,
                payment_method="card",
                user_id=user_id,
                amount=6000.00
            )

            payment_id = created_payment["payment_id"]

            # Retrieve payment via service
            retrieved_payment = payment_service.get_payment(payment_id)

            # Assert - Retrieved payment matches created
            assert retrieved_payment is not None
            assert retrieved_payment["payment_id"] == payment_id
            assert retrieved_payment["order_id"] == order_id
            assert retrieved_payment["amount"] == 6000.00
            assert retrieved_payment["user_id"] == user_id

            # Wait for async processing
            await asyncio.sleep(5.5)

            # Retrieve updated payment
            updated_payment = payment_service.get_payment(payment_id)

            # Assert - Payment was updated correctly
            assert updated_payment["status"] == "succeeded"
            assert updated_payment["paid_at"] is not None

    def test_api_endpoint_service_integration(self, test_client: TestClient):
        """
        Test integration between API endpoints and service layer.

        This test verifies:
        1. API endpoint properly calls service methods
        2. Service responses are correctly transformed to API responses
        3. Error handling flows correctly through layers
        """
        # Arrange
        payload = {
            "order_id": "ord_api_service_001",
            "payment_method": "card"
        }

        mock_publisher = Mock(spec=RabbitMQPublisher)
        mock_publisher.publish_payment_success = AsyncMock()

        with patch("app.services.payment_service.rabbitmq_publisher", mock_publisher):
            # Act 1 - Create payment via API
            create_response = test_client.post("/api/payments", json=payload)

            # Assert 1 - API transforms service response correctly
            assert create_response.status_code == 201
            api_data = create_response.json()
            assert "payment_id" in api_data
            assert "confirmation_url" in api_data
            assert api_data["order_id"] == "ord_api_service_001"

            # Verify service layer has the data
            from app.services import payment_service
            service_data = payment_service.get_payment(api_data["payment_id"])
            assert service_data is not None
            assert service_data["payment_id"] == api_data["payment_id"]
            assert service_data["order_id"] == api_data["order_id"]

            # Act 2 - Get payment via API
            status_response = test_client.get(f"/api/payments/{api_data['payment_id']}")

            # Assert 2 - API correctly retrieves from service
            assert status_response.status_code == 200
            status_data = status_response.json()
            assert status_data["payment_id"] == service_data["payment_id"]
            assert status_data["status"] == service_data["status"]
