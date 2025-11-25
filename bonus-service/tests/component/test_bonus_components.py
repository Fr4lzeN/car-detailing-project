"""
Component tests for bonus-service

These tests verify interactions between multiple components (API endpoints, services, repositories)
while maintaining isolation from external systems through mocking. Component tests ensure that
different layers of the application work together correctly.

Test scenarios:
1. Promocode application with discount validation
2. Bonus spending with balance verification
3. Bonus accrual via RabbitMQ consumer (mocked)
4. Promocode validation (active/inactive states)
5. Complex scenario: accrue -> apply -> spend bonuses
"""

import pytest
import json
from uuid import UUID, uuid4
from unittest.mock import Mock, AsyncMock, patch
from httpx import AsyncClient
from fastapi import FastAPI

from app.repositories.local_bonus_repo import LocalBonusRepository, Promocode
from app.services.bonus_service import BonusService
from app.services.rabbitmq_consumer import RabbitMQConsumer
from app.endpoints import bonuses
from app.models.bonus import HealthResponse
from app.config import settings


# ==================== Component Test Fixtures ====================

@pytest.fixture
def component_repository() -> LocalBonusRepository:
    """
    Create a fresh repository instance for component testing.
    This provides real repository behavior with isolated in-memory storage.
    """
    return LocalBonusRepository()


@pytest.fixture
def component_bonus_service(component_repository: LocalBonusRepository) -> BonusService:
    """
    Create a bonus service with a real repository for component testing.
    This allows testing the full interaction between service and repository layers.
    """
    return BonusService(repository=component_repository)


@pytest.fixture
async def component_test_client(component_repository: LocalBonusRepository) -> AsyncClient:
    """
    Create an async test client with dependency injection for component testing.
    Uses real service and repository instances to test full component interaction.
    """
    from app.auth import get_current_user_id
    from httpx import ASGITransport

    # Create test app without lifespan (to avoid RabbitMQ connection)
    test_app = FastAPI(title="Test Bonus Service - Component")

    # Mock JWT auth to return test user
    test_user_id = UUID("c3f4e1a1-5b8a-4b0e-8d9b-9d4a6f1e2c3d")

    def mock_get_current_user_id() -> UUID:
        return test_user_id

    test_app.dependency_overrides[get_current_user_id] = mock_get_current_user_id

    # Override the bonus_service dependency with our component service
    # This ensures all endpoints use the same repository instance
    component_service = BonusService(repository=component_repository)

    # Monkey-patch the module-level bonus_service
    original_service = bonuses.bonus_service
    bonuses.bonus_service = component_service

    test_app.include_router(bonuses.router)

    @test_app.get("/health", response_model=HealthResponse)
    async def health_check():
        return HealthResponse(status="healthy", service=settings.SERVICE_NAME)

    # Use ASGITransport for httpx 0.28+
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield client

    # Restore original service
    bonuses.bonus_service = original_service


@pytest.fixture
def test_user_id() -> UUID:
    """Standard test user ID for component tests"""
    return UUID("c3f4e1a1-5b8a-4b0e-8d9b-9d4a6f1e2c3d")


@pytest.fixture
def test_order_id() -> UUID:
    """Standard test order ID for component tests"""
    return UUID("123e4567-e89b-12d3-a456-426614174000")


# ==================== Component Test 1: Promocode Application with Discount Validation ====================

@pytest.mark.asyncio
class TestPromocodeApplication:
    """Test promocode application through full API -> Service -> Repository flow"""

    async def test_apply_valid_promocode_returns_correct_discount(
        self,
        component_test_client: AsyncClient,
        test_order_id: UUID
    ):
        """
        Component Test 1: Apply valid promocode and verify correct discount amount

        Tests the full flow:
        - API endpoint receives request
        - Service validates promocode
        - Repository finds active promocode
        - Response contains correct discount amount
        """
        # Arrange
        payload = {
            "order_id": str(test_order_id),
            "promocode": "SUMMER24"
        }

        # Act
        response = await component_test_client.post(
            "/api/bonuses/promocodes/apply",
            json=payload
        )

        # Assert
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert data["order_id"] == str(test_order_id)
        assert data["promocode"] == "SUMMER24"
        assert data["status"] == "applied"
        assert data["discount_amount"] == 500.0, "SUMMER24 should give 500 RUB discount"

    async def test_apply_invalid_promocode_returns_404(
        self,
        component_test_client: AsyncClient,
        test_order_id: UUID
    ):
        """
        Test that invalid promocode returns 404 NOT_FOUND

        Verifies error handling through all layers:
        - Repository returns None for invalid code
        - Service raises ValueError
        - Endpoint converts to HTTP 404
        """
        # Arrange
        payload = {
            "order_id": str(test_order_id),
            "promocode": "INVALID_CODE"
        }

        # Act
        response = await component_test_client.post(
            "/api/bonuses/promocodes/apply",
            json=payload
        )

        # Assert
        assert response.status_code == 404, f"Expected 404 for invalid promocode, got {response.status_code}"
        assert "invalid or inactive" in response.json()["detail"].lower()

    async def test_apply_different_valid_promocode(
        self,
        component_test_client: AsyncClient,
        test_order_id: UUID
    ):
        """
        Test applying WELCOME10 promocode with higher discount

        Verifies that multiple promocodes work correctly with different discount amounts
        """
        # Arrange
        payload = {
            "order_id": str(test_order_id),
            "promocode": "WELCOME10"
        }

        # Act
        response = await component_test_client.post(
            "/api/bonuses/promocodes/apply",
            json=payload
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["promocode"] == "WELCOME10"
        assert data["discount_amount"] == 1000.0, "WELCOME10 should give 1000 RUB discount"


# ==================== Component Test 2: Bonus Spending with Balance Verification ====================

@pytest.mark.asyncio
class TestBonusSpending:
    """Test bonus spending through full component stack with balance management"""

    async def test_spend_bonuses_with_sufficient_balance(
        self,
        component_test_client: AsyncClient,
        component_repository: LocalBonusRepository,
        test_user_id: UUID,
        test_order_id: UUID
    ):
        """
        Component Test 2: Spend bonuses when user has sufficient balance

        Tests the full flow:
        - Pre-populate user balance in repository
        - API endpoint receives spend request
        - Service validates balance
        - Repository updates balance
        - Response reflects new balance
        """
        # Arrange: Add bonuses to user account
        initial_balance = 1000.0
        await component_repository.add_bonuses(test_user_id, initial_balance)

        spend_amount = 300
        payload = {
            "order_id": str(test_order_id),
            "amount": spend_amount
        }

        # Act
        response = await component_test_client.post(
            "/api/bonuses/spend",
            json=payload
        )

        # Assert
        assert response.status_code == 200

        data = response.json()
        assert data["order_id"] == str(test_order_id)
        assert data["bonuses_spent"] == spend_amount
        assert data["new_balance"] == initial_balance - spend_amount

        # Verify repository state
        final_balance = await component_repository.get_user_balance(test_user_id)
        assert final_balance == 700.0, "Balance should be updated in repository"

    async def test_spend_bonuses_with_insufficient_balance_returns_400(
        self,
        component_test_client: AsyncClient,
        component_repository: LocalBonusRepository,
        test_user_id: UUID,
        test_order_id: UUID
    ):
        """
        Test that spending more bonuses than available returns 400 BAD_REQUEST

        Verifies error handling:
        - Repository reports insufficient balance
        - Service raises ValueError
        - Endpoint converts to HTTP 400
        """
        # Arrange: User has only 100 bonuses
        await component_repository.add_bonuses(test_user_id, 100.0)

        payload = {
            "order_id": str(test_order_id),
            "amount": 500  # Try to spend more than available
        }

        # Act
        response = await component_test_client.post(
            "/api/bonuses/spend",
            json=payload
        )

        # Assert
        assert response.status_code == 400, f"Expected 400 for insufficient bonuses, got {response.status_code}"
        assert "insufficient" in response.json()["detail"].lower()

        # Verify balance unchanged
        balance = await component_repository.get_user_balance(test_user_id)
        assert balance == 100.0, "Balance should remain unchanged after failed spend"

    async def test_spend_zero_balance_user_returns_400(
        self,
        component_test_client: AsyncClient,
        test_user_id: UUID,
        test_order_id: UUID
    ):
        """
        Test spending bonuses when user has zero balance

        Verifies that new users (no balance entry) cannot spend bonuses
        """
        # Arrange: User has no bonuses (default 0)
        payload = {
            "order_id": str(test_order_id),
            "amount": 50
        }

        # Act
        response = await component_test_client.post(
            "/api/bonuses/spend",
            json=payload
        )

        # Assert
        assert response.status_code == 400
        assert "insufficient" in response.json()["detail"].lower()


# ==================== Component Test 3: Bonus Accrual via RabbitMQ Consumer ====================

@pytest.mark.asyncio
class TestBonusAccrualViaRabbitMQ:
    """Test bonus accrual through RabbitMQ consumer with mocked message broker"""

    async def test_rabbitmq_consumer_accrues_bonuses_on_payment_success(
        self,
        component_bonus_service: BonusService,
        component_repository: LocalBonusRepository,
        test_user_id: UUID,
        test_order_id: UUID
    ):
        """
        Component Test 3: RabbitMQ consumer accrues bonuses on payment_succeeded event

        Tests the message processing flow:
        - Mock RabbitMQ message with payment data
        - Consumer processes message
        - Service accrues bonuses (1% of payment)
        - Repository updates user balance
        """
        # Arrange
        payment_amount = 10000.0  # 10,000 RUB payment
        expected_bonuses = payment_amount * 0.01  # 1% = 100 bonuses

        # Create consumer with real service
        consumer = RabbitMQConsumer(bonus_service=component_bonus_service)

        # Create mock RabbitMQ message with proper async context manager
        mock_message = Mock()
        message_body = {
            "order_id": str(test_order_id),
            "user_id": str(test_user_id),
            "amount": payment_amount
        }
        mock_message.body = json.dumps(message_body).encode()

        # Create a proper async context manager for message.process()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=None)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        mock_message.process = Mock(return_value=mock_context)

        # Act: Process the message
        await consumer.on_message(mock_message)

        # Assert: Verify bonuses were accrued
        balance = await component_repository.get_user_balance(test_user_id)
        assert balance == expected_bonuses, f"Expected {expected_bonuses} bonuses, got {balance}"

    async def test_rabbitmq_consumer_handles_multiple_payments_cumulatively(
        self,
        component_bonus_service: BonusService,
        component_repository: LocalBonusRepository,
        test_user_id: UUID
    ):
        """
        Test that multiple payment events accumulate bonuses correctly

        Verifies:
        - Multiple messages are processed independently
        - Bonuses accumulate in user balance
        - Repository maintains state across messages
        """
        # Arrange
        consumer = RabbitMQConsumer(bonus_service=component_bonus_service)

        payments = [
            {"order_id": str(uuid4()), "amount": 5000.0},   # +50 bonuses
            {"order_id": str(uuid4()), "amount": 3000.0},   # +30 bonuses
            {"order_id": str(uuid4()), "amount": 2000.0},   # +20 bonuses
        ]

        # Act: Process multiple payment messages
        for payment_data in payments:
            mock_message = Mock()
            message_body = {
                **payment_data,
                "user_id": str(test_user_id)
            }
            mock_message.body = json.dumps(message_body).encode()

            # Create proper async context manager
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=None)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_message.process = Mock(return_value=mock_context)

            await consumer.on_message(mock_message)

        # Assert: Total bonuses should be sum of all accruals
        total_expected = (5000 + 3000 + 2000) * 0.01  # 100 bonuses
        balance = await component_repository.get_user_balance(test_user_id)
        assert balance == total_expected, f"Expected cumulative {total_expected} bonuses, got {balance}"

    async def test_rabbitmq_consumer_handles_invalid_message_gracefully(
        self,
        component_bonus_service: BonusService,
        component_repository: LocalBonusRepository,
        test_user_id: UUID
    ):
        """
        Test that consumer handles malformed messages without crashing

        Verifies error handling:
        - Invalid JSON in message body
        - Missing required fields
        - Consumer logs error but doesn't crash
        """
        # Arrange
        consumer = RabbitMQConsumer(bonus_service=component_bonus_service)

        # Create mock message with invalid JSON
        mock_message = Mock()
        mock_message.body = b'{"invalid": "missing required fields"}'

        # Create proper async context manager
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=None)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        mock_message.process = Mock(return_value=mock_context)

        # Act: Process invalid message (should not raise exception)
        await consumer.on_message(mock_message)

        # Assert: Balance should remain 0 (no bonuses accrued)
        balance = await component_repository.get_user_balance(test_user_id)
        assert balance == 0.0, "Invalid message should not accrue bonuses"


# ==================== Component Test 4: Promocode Validation (Active/Inactive) ====================

@pytest.mark.asyncio
class TestPromocodeValidation:
    """Test promocode validation logic through repository and service layers"""

    async def test_inactive_promocode_is_rejected(
        self,
        component_bonus_service: BonusService,
        component_repository: LocalBonusRepository,
        test_order_id: UUID
    ):
        """
        Component Test 4: Inactive promocodes should be rejected

        Tests validation flow:
        - Add inactive promocode to repository
        - Service attempts to apply it
        - Repository filters out inactive codes
        - Service raises ValueError
        """
        # Arrange: Add an inactive promocode
        inactive_promo = Promocode(code="EXPIRED", discount_amount=200.0, active=False)
        component_repository.promocodes.append(inactive_promo)

        # Act & Assert: Attempting to apply should raise ValueError
        with pytest.raises(ValueError, match="invalid or inactive"):
            await component_bonus_service.apply_promocode(
                order_id=test_order_id,
                promocode="EXPIRED"
            )

    async def test_active_promocode_is_accepted(
        self,
        component_bonus_service: BonusService,
        test_order_id: UUID
    ):
        """
        Test that active promocodes are successfully applied

        Verifies the default SUMMER24 and WELCOME10 promocodes are active
        """
        # Act: Apply active promocode
        status, discount = await component_bonus_service.apply_promocode(
            order_id=test_order_id,
            promocode="SUMMER24"
        )

        # Assert
        assert status == "applied"
        assert discount == 500.0

    async def test_case_sensitive_promocode_matching(
        self,
        component_bonus_service: BonusService,
        test_order_id: UUID
    ):
        """
        Test that promocode matching is case-sensitive

        Verifies that lowercase/uppercase variations are rejected
        """
        # Act & Assert: Lowercase version should fail
        with pytest.raises(ValueError, match="invalid or inactive"):
            await component_bonus_service.apply_promocode(
                order_id=test_order_id,
                promocode="summer24"  # lowercase
            )


# ==================== Component Test 5: Complex Scenario - Accrue, Apply, Spend ====================

@pytest.mark.asyncio
class TestComplexBonusScenario:
    """Test complete user journey through bonus system"""

    async def test_complete_bonus_lifecycle_accrue_then_spend(
        self,
        component_test_client: AsyncClient,
        component_bonus_service: BonusService,
        component_repository: LocalBonusRepository,
        test_user_id: UUID
    ):
        """
        Component Test 5: Complete bonus lifecycle scenario

        Scenario:
        1. User makes payment -> bonuses accrued via RabbitMQ (100 bonuses)
        2. User applies promocode -> gets discount (500 RUB)
        3. User spends bonuses -> balance reduced

        Tests full integration:
        - RabbitMQ consumer (mocked) -> Service -> Repository
        - API endpoint -> Service -> Repository
        - State persistence across operations
        """
        # ========== Step 1: Accrue bonuses from payment ==========
        payment_amount = 10000.0  # 10,000 RUB
        expected_accrued = 100.0   # 1% = 100 bonuses

        # Simulate RabbitMQ message processing
        consumer = RabbitMQConsumer(bonus_service=component_bonus_service)
        mock_message = Mock()
        message_body = {
            "order_id": str(uuid4()),
            "user_id": str(test_user_id),
            "amount": payment_amount
        }
        mock_message.body = json.dumps(message_body).encode()

        # Create proper async context manager
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=None)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        mock_message.process = Mock(return_value=mock_context)

        await consumer.on_message(mock_message)

        # Verify bonuses accrued
        balance_after_accrual = await component_repository.get_user_balance(test_user_id)
        assert balance_after_accrual == expected_accrued, "Step 1: Bonuses should be accrued"

        # ========== Step 2: Apply promocode to new order ==========
        order_id_for_promo = uuid4()
        promo_payload = {
            "order_id": str(order_id_for_promo),
            "promocode": "SUMMER24"
        }

        promo_response = await component_test_client.post(
            "/api/bonuses/promocodes/apply",
            json=promo_payload
        )

        assert promo_response.status_code == 200, "Step 2: Promocode should be applied"
        promo_data = promo_response.json()
        assert promo_data["discount_amount"] == 500.0, "Step 2: Should get 500 RUB discount"

        # Balance should remain unchanged (promocode doesn't affect bonuses)
        balance_after_promo = await component_repository.get_user_balance(test_user_id)
        assert balance_after_promo == expected_accrued, "Step 2: Balance unchanged by promocode"

        # ========== Step 3: Spend bonuses on another order ==========
        order_id_for_spend = uuid4()
        spend_amount = 50
        spend_payload = {
            "order_id": str(order_id_for_spend),
            "amount": spend_amount
        }

        spend_response = await component_test_client.post(
            "/api/bonuses/spend",
            json=spend_payload
        )

        assert spend_response.status_code == 200, "Step 3: Bonuses should be spent"
        spend_data = spend_response.json()
        assert spend_data["bonuses_spent"] == spend_amount, "Step 3: Should spend 50 bonuses"
        assert spend_data["new_balance"] == expected_accrued - spend_amount, "Step 3: Balance should be reduced"

        # ========== Final Verification ==========
        final_balance = await component_repository.get_user_balance(test_user_id)
        assert final_balance == 50.0, "Final: User should have 50 bonuses remaining"

    async def test_multiple_users_isolated_balances(
        self,
        component_bonus_service: BonusService,
        component_repository: LocalBonusRepository
    ):
        """
        Test that multiple users have isolated bonus balances

        Verifies:
        - Each user has independent balance
        - Operations on one user don't affect others
        - Repository correctly isolates user data
        """
        # Arrange
        user1_id = UUID("11111111-1111-1111-1111-111111111111")
        user2_id = UUID("22222222-2222-2222-2222-222222222222")
        user3_id = UUID("33333333-3333-3333-3333-333333333333")

        # Act: Accrue different amounts to each user
        await component_repository.add_bonuses(user1_id, 100.0)
        await component_repository.add_bonuses(user2_id, 200.0)
        await component_repository.add_bonuses(user3_id, 300.0)

        # Spend some bonuses from user2
        await component_repository.spend_bonuses(user2_id, 50)

        # Assert: Verify each user's balance is independent
        balance1 = await component_repository.get_user_balance(user1_id)
        balance2 = await component_repository.get_user_balance(user2_id)
        balance3 = await component_repository.get_user_balance(user3_id)

        assert balance1 == 100.0, "User 1 balance should be unchanged"
        assert balance2 == 150.0, "User 2 balance should be reduced by 50"
        assert balance3 == 300.0, "User 3 balance should be unchanged"

    async def test_concurrent_bonus_operations_maintain_consistency(
        self,
        component_bonus_service: BonusService,
        component_repository: LocalBonusRepository,
        test_user_id: UUID
    ):
        """
        Test that multiple bonus operations maintain balance consistency

        Simulates concurrent operations:
        - Multiple accruals
        - Multiple spends
        - Final balance should be mathematically consistent
        """
        # Arrange: Start with initial balance
        await component_repository.add_bonuses(test_user_id, 500.0)

        # Act: Perform multiple operations
        operations = [
            ("add", 100.0),
            ("spend", 50),
            ("add", 200.0),
            ("spend", 100),
            ("add", 150.0),
        ]

        for operation, amount in operations:
            if operation == "add":
                await component_repository.add_bonuses(test_user_id, amount)
            elif operation == "spend":
                await component_repository.spend_bonuses(test_user_id, int(amount))

        # Assert: Final balance should be correct
        # Initial: 500, +100, -50, +200, -100, +150 = 800
        expected_final = 500.0 + 100.0 - 50 + 200.0 - 100 + 150.0
        final_balance = await component_repository.get_user_balance(test_user_id)
        assert final_balance == expected_final, f"Expected {expected_final}, got {final_balance}"
