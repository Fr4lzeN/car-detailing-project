# Component Tests for Order Service

## Overview

Component tests verify the interaction between multiple components of the order-service, ensuring that different layers (API endpoints, business logic, repositories, external clients) work together correctly.

## Test Structure

The component tests are organized into the following test classes:

### 1. TestOrderCreationWithCarServiceIntegration

Tests the complete order creation flow with car-service HTTP integration.

**Tests:**
- `test_create_order_with_car_service_success` - Successful order creation with car verification
- `test_create_order_car_service_returns_404` - Order creation fails when car not found
- `test_create_order_car_service_timeout` - Order creation handles car-service timeout

**Components tested:**
- API endpoint: `POST /api/orders`
- OrderService business logic
- CarServiceClient HTTP client (mocked)
- LocalOrderRepository

### 2. TestOrderStatusTransitionValidation

Tests order status updates with validation logic.

**Tests:**
- `test_valid_status_transitions_sequence` - Complete valid transition workflow (created → in_progress → work_completed → car_issued)
- `test_invalid_status_transition_skipping_steps` - Invalid transition is rejected
- `test_terminal_state_car_issued_rejects_updates` - Terminal state cannot be changed

**Components tested:**
- API endpoint: `PATCH /api/orders/{order_id}/status`
- OrderService status transition validation
- LocalOrderRepository state management

### 3. TestReviewManagement

Tests review creation and validation.

**Tests:**
- `test_add_review_to_existing_order` - Successfully adding a review
- `test_prevent_duplicate_reviews` - Duplicate reviews are rejected with 409
- `test_add_review_order_not_found` - Review creation for non-existent order fails

**Components tested:**
- API endpoint: `POST /api/orders/review`
- OrderService review validation
- LocalOrderRepository review storage

### 4. TestCompleteOrderLifecycle

End-to-end test for complete order lifecycle.

**Test:**
- `test_complete_order_workflow_from_creation_to_review` - Complete workflow from order creation to review

**Workflow:**
1. Create order with car-service verification
2. Update status: created → in_progress
3. Update status: in_progress → work_completed
4. Add customer review
5. Update status: work_completed → car_issued
6. Verify terminal state cannot be changed

**All components tested together in realistic scenario**

## Key Testing Patterns

### Mocking External Dependencies

```python
@patch('app.services.car_client.car_client.verify_car_exists')
def test_example(self, mock_verify_car, test_client):
    mock_verify_car.return_value = True
    # ... test code
```

External HTTP calls to car-service are mocked to ensure test isolation.

### Repository Cleanup

```python
@pytest.fixture(autouse=True)
def clean_repository():
    from app.repositories.local_order_repo import order_repository
    order_repository._orders.clear()
    # ... cleanup code
```

The `autouse=True` fixture ensures the repository is cleaned before and after each test, maintaining test isolation.

### Authentication Bypass

```python
def mock_get_current_user_id():
    return TEST_USER_ID

app.dependency_overrides[get_current_user_id] = mock_get_current_user_id
```

Authentication is mocked to focus tests on business logic, not auth mechanisms.

## Running Tests

### Run only component tests:
```bash
pytest tests/component/ -v
```

### Run with coverage:
```bash
pytest tests/component/ --cov=app --cov-report=html
```

### Run all tests (unit + integration + component):
```bash
pytest tests/ -v
```

## Test Coverage

Component tests contribute to the following coverage areas:
- **API Endpoints**: 100% coverage of order management endpoints
- **Business Logic**: 96% coverage of OrderService
- **Repository**: 100% coverage of LocalOrderRepository
- **HTTP Client**: 100% coverage of CarServiceClient (through mocks)

**Overall coverage with all tests: 89.69%**

## Test Isolation

Each test is completely isolated:
- Repository is cleaned before and after each test
- External HTTP calls are mocked
- No shared state between tests
- Tests can run in any order

## Best Practices

1. **Descriptive Test Names**: Each test name clearly describes what is being tested
2. **AAA Pattern**: Tests follow Arrange-Act-Assert structure
3. **Comprehensive Comments**: Each test includes detailed scenario documentation
4. **Mock Verification**: Mocks are verified to ensure expected interactions
5. **State Validation**: Repository state is checked to ensure correct data persistence

## Difference from Integration Tests

**Integration Tests** (`tests/integration/`):
- Test API endpoints in isolation
- Focus on HTTP request/response validation
- Mock all dependencies

**Component Tests** (`tests/component/`):
- Test interaction between multiple components
- Verify business logic flows through multiple layers
- Test realistic workflows (e.g., complete order lifecycle)
- Ensure components work together correctly

## Future Enhancements

Potential areas for additional component tests:
- Concurrent order creation handling
- Error propagation through component stack
- Performance testing of component interactions
- Edge cases in multi-step workflows
