# Component Tests for Payment Service

This directory contains component tests that verify the interaction between multiple components of the payment service.

## Overview

Component tests sit between unit tests and integration tests. They test how multiple components work together while keeping external dependencies (like RabbitMQ) mocked.

## Test Structure

The test suite is organized into 6 test classes with 11 total tests:

### 1. TestPaymentCreationComponent (1 test)
Tests the complete flow of payment creation through API -> Service -> Repository layers.

- `test_create_payment_with_pending_status`: Verifies payment creation returns pending status and stores data correctly in repository

### 2. TestPaymentStatusRetrievalComponent (1 test)
Tests payment status retrieval workflow.

- `test_get_payment_status_flow`: Verifies complete flow: create payment -> retrieve status via API -> validate repository data

### 3. TestRabbitMQPublishingComponent (1 test)
Tests RabbitMQ event publishing integration.

- `test_payment_success_event_publishing`: Verifies async payment processing publishes events to RabbitMQ with correct data

### 4. TestAsyncPaymentProcessingComponent (1 test)
Tests asynchronous payment processing workflow.

- `test_payment_status_changes_to_succeeded`: Verifies payment status transitions from pending to succeeded after async processing

### 5. TestCompletePaymentWorkflowComponent (3 tests)
Tests end-to-end payment workflows.

- `test_end_to_end_payment_workflow_sync`: Complete workflow from creation to status update
- `test_payment_workflow_with_duplicate_prevention`: Prevents duplicate payments for same order
- `test_multiple_payments_different_orders_workflow`: Multiple payments can be created independently

### 6. TestPaymentComponentErrorHandling (2 tests)
Tests error handling across component boundaries.

- `test_payment_processing_failure_handling`: Handles RabbitMQ publishing failures gracefully
- `test_payment_not_found_component_flow`: Proper error handling for missing payments

### 7. TestPaymentComponentIntegration (2 tests)
Tests integration between service layers.

- `test_repository_service_integration`: Repository and service layer integration
- `test_api_endpoint_service_integration`: API endpoint and service layer integration

## Running the Tests

### Run all component tests
```bash
cd /Users/fr4lzen/Documents/мирэа/микросы/пр7/project/payment-service
source venv/bin/activate
pytest tests/component/ -v
```

### Run specific test class
```bash
pytest tests/component/test_payment_components.py::TestPaymentCreationComponent -v
```

### Run with coverage
```bash
pytest tests/component/ --cov=app --cov-report=html
```

## Key Features

### Mocked Dependencies
- RabbitMQ publisher is mocked using `unittest.mock.Mock` and `AsyncMock`
- JWT authentication is mocked via `conftest.py` fixtures
- External dependencies are isolated

### Async Support
- Tests use `@pytest.mark.asyncio` for async operations
- Properly handles asyncio.sleep for payment processing simulation
- Uses AsyncMock for async RabbitMQ methods

### Component Verification
Each test verifies multiple layers:
1. API endpoint receives and validates requests
2. Service layer processes business logic
3. Repository layer stores/retrieves data
4. RabbitMQ publisher sends events
5. Error handling works across layers

## Test Data

Tests use isolated test data:
- Order IDs: `ord_component_test_001`, `ord_e2e_test_001`, etc.
- Payment IDs: Auto-generated with `pay_` prefix
- User IDs: Generated using `uuid4()`

## Important Notes

1. **Test Isolation**: Each test uses fresh repository state via `reset_singletons` fixture
2. **RabbitMQ Mocking**: All RabbitMQ operations are mocked - no real message broker needed
3. **Async Timing**: Tests that verify async processing use `asyncio.sleep(5.5)` to wait for 5-second payment processing
4. **TestClient Limitations**: FastAPI TestClient doesn't support background tasks fully, so some tests manually simulate processing

## Coverage

Component tests contribute to overall code coverage:
- Endpoints: 87% coverage
- Services: 90% coverage
- Repositories: 93% coverage
- Overall: 69% (component tests alone)

## Future Enhancements

Potential additions:
- Tests for payment timeout scenarios
- Tests for concurrent payment creation
- Tests for payment cancellation workflow
- Tests for different payment methods (SBP vs card)
- Performance tests for high-volume scenarios
