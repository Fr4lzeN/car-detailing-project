"""
Component tests for Cart Service

Component tests verify the interaction between multiple components of the service:
- API endpoints (FastAPI routes)
- Service layer (business logic)
- Repository layer (data storage)
- CATALOG integration (product/service catalog)

These tests use real instances of all internal components (no mocking)
to validate end-to-end behavior and cross-component interactions.
"""
import pytest
from fastapi.testclient import TestClient


class TestAddItemFromCatalog:
    """
    Component Test 1: Test adding items from catalog to cart

    This test validates the complete flow from API request through service
    layer to repository, ensuring catalog items are correctly validated,
    transformed, and stored.
    """

    def test_add_oil_change_service_from_catalog(self, test_client: TestClient):
        """
        Test adding 'Замена масла' service from catalog to cart

        Validates:
        - API endpoint accepts valid request
        - Service validates item exists in CATALOG
        - Service extracts correct name and price from CATALOG
        - Repository stores item correctly
        - Response includes correct catalog data
        """
        # Arrange
        request_data = {
            "item_id": "svc_oil_change",
            "type": "service",
            "quantity": 1
        }

        # Act
        response = test_client.post("/api/cart/items", json=request_data)

        # Assert
        assert response.status_code == 200
        data = response.json()

        # Verify cart structure
        assert "user_id" in data
        assert "items" in data
        assert "total_price" in data

        # Verify item was added from catalog with correct data
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["item_id"] == "svc_oil_change"
        assert item["type"] == "service"
        assert item["name"] == "Замена масла"  # From CATALOG
        assert item["price"] == 2500.00  # From CATALOG
        assert item["quantity"] == 1

        # Verify total price calculation
        assert data["total_price"] == 2500.00

    def test_add_oil_filter_product_from_catalog(self, test_client: TestClient):
        """
        Test adding 'Масляный фильтр' product from catalog to cart

        Validates:
        - Product items are handled correctly
        - Quantity multiplier works for products
        - Catalog lookup works for products, not just services
        """
        # Arrange
        request_data = {
            "item_id": "prod_oil_filter",
            "type": "product",
            "quantity": 3
        }

        # Act
        response = test_client.post("/api/cart/items", json=request_data)

        # Assert
        assert response.status_code == 200
        data = response.json()

        # Verify product added from catalog
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["item_id"] == "prod_oil_filter"
        assert item["type"] == "product"
        assert item["name"] == "Масляный фильтр"  # From CATALOG
        assert item["price"] == 1000.00  # From CATALOG
        assert item["quantity"] == 3

        # Verify price calculation: 1000.00 * 3 = 3000.00
        assert data["total_price"] == 3000.00

    def test_add_diagnostics_service_from_catalog(self, test_client: TestClient):
        """
        Test adding 'Диагностика' service from catalog to cart

        Validates:
        - All catalog items are accessible
        - Different services have different prices
        """
        # Arrange
        request_data = {
            "item_id": "svc_diagnostics",
            "type": "service",
            "quantity": 2
        }

        # Act
        response = test_client.post("/api/cart/items", json=request_data)

        # Assert
        assert response.status_code == 200
        data = response.json()

        # Verify diagnostics service added from catalog
        item = data["items"][0]
        assert item["item_id"] == "svc_diagnostics"
        assert item["name"] == "Диагностика"  # From CATALOG
        assert item["price"] == 1500.00  # From CATALOG
        assert item["quantity"] == 2

        # Verify price calculation: 1500.00 * 2 = 3000.00
        assert data["total_price"] == 3000.00

    def test_add_nonexistent_item_fails_catalog_validation(self, test_client: TestClient):
        """
        Test that adding item not in catalog returns 404

        Validates:
        - Service layer validates against CATALOG
        - Proper error handling for missing catalog items
        - Cart remains unchanged after failed add
        """
        # Arrange
        request_data = {
            "item_id": "non_existent_item",
            "type": "service",
            "quantity": 1
        }

        # Act
        response = test_client.post("/api/cart/items", json=request_data)

        # Assert
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "not found in catalog" in data["detail"].lower()

        # Verify cart is still empty
        get_response = test_client.get("/api/cart")
        cart_data = get_response.json()
        assert len(cart_data["items"]) == 0
        assert cart_data["total_price"] == 0.0

    def test_add_item_with_wrong_type_fails_validation(self, test_client: TestClient):
        """
        Test that type mismatch between request and catalog returns 400

        Validates:
        - Service validates type matches catalog
        - Business rules are enforced at service layer
        """
        # Arrange - svc_oil_change is a service, not a product
        request_data = {
            "item_id": "svc_oil_change",
            "type": "product",  # Wrong type!
            "quantity": 1
        }

        # Act
        response = test_client.post("/api/cart/items", json=request_data)

        # Assert
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "type mismatch" in data["detail"].lower()


class TestRemoveItemFromCart:
    """
    Component Test 2: Test removing items from cart

    Validates the complete flow of item removal including:
    - API endpoint handling
    - Service layer validation
    - Repository state updates
    - Error handling for missing items
    """

    def test_remove_existing_item_success(self, test_client: TestClient):
        """
        Test removing an item that exists in cart

        Validates:
        - Item can be added and then removed
        - Repository correctly deletes item
        - 204 No Content response is returned
        - Cart is empty after removal
        """
        # Arrange - Add item first
        test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 1}
        )

        # Verify item was added
        get_response = test_client.get("/api/cart")
        assert len(get_response.json()["items"]) == 1

        # Act - Remove the item
        delete_response = test_client.delete("/api/cart/items/svc_oil_change")

        # Assert
        assert delete_response.status_code == 204
        assert delete_response.text == ""  # No content

        # Verify item was removed from repository
        get_response = test_client.get("/api/cart")
        cart_data = get_response.json()
        assert len(cart_data["items"]) == 0
        assert cart_data["total_price"] == 0.0

    def test_remove_nonexistent_item_returns_404(self, test_client: TestClient):
        """
        Test removing an item that doesn't exist in cart

        Validates:
        - Service validates item exists before removal
        - Proper error response for missing items
        - Repository returns False for nonexistent items
        """
        # Act - Try to remove item from empty cart
        response = test_client.delete("/api/cart/items/svc_oil_change")

        # Assert
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "not found in cart" in data["detail"].lower()

    def test_remove_one_item_preserves_others(self, test_client: TestClient):
        """
        Test removing one item doesn't affect other items in cart

        Validates:
        - Repository correctly removes only specified item
        - Other items remain intact
        - Total price recalculates correctly
        """
        # Arrange - Add multiple items
        test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 1}
        )
        test_client.post(
            "/api/cart/items",
            json={"item_id": "prod_oil_filter", "type": "product", "quantity": 2}
        )
        test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_diagnostics", "type": "service", "quantity": 1}
        )

        # Verify all items added
        get_response = test_client.get("/api/cart")
        assert len(get_response.json()["items"]) == 3

        # Act - Remove middle item
        delete_response = test_client.delete("/api/cart/items/prod_oil_filter")
        assert delete_response.status_code == 204

        # Assert - Verify other items preserved
        get_response = test_client.get("/api/cart")
        cart_data = get_response.json()

        assert len(cart_data["items"]) == 2

        # Verify specific items remain
        item_ids = {item["item_id"] for item in cart_data["items"]}
        assert "svc_oil_change" in item_ids
        assert "svc_diagnostics" in item_ids
        assert "prod_oil_filter" not in item_ids

        # Verify total price: 2500.00 + 1500.00 = 4000.00
        assert cart_data["total_price"] == 4000.00


class TestTotalPriceCalculation:
    """
    Component Test 3: Test total_price calculation with different quantities

    Validates that the service layer correctly calculates total_price
    across different scenarios and quantities.
    """

    def test_total_price_single_item_quantity_one(self, test_client: TestClient):
        """
        Test total price calculation for single item with quantity 1

        Validates:
        - Basic price calculation: price * 1
        - Service._calculate_total_price() works correctly
        """
        # Arrange & Act
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 1}
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        # Expected: 2500.00 * 1 = 2500.00
        assert data["total_price"] == 2500.00

    def test_total_price_single_item_multiple_quantity(self, test_client: TestClient):
        """
        Test total price calculation for single item with quantity > 1

        Validates:
        - Quantity multiplier works correctly
        - Price calculation: price * quantity
        """
        # Arrange & Act
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "prod_oil_filter", "type": "product", "quantity": 5}
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        # Expected: 1000.00 * 5 = 5000.00
        assert data["total_price"] == 5000.00

    def test_total_price_multiple_items_different_quantities(self, test_client: TestClient):
        """
        Test total price calculation for multiple items with different quantities

        Validates:
        - Service sums all items correctly
        - Each item's price * quantity is calculated
        - Repository maintains all items for calculation
        """
        # Arrange & Act - Add three different items with different quantities
        test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 2}
        )
        test_client.post(
            "/api/cart/items",
            json={"item_id": "prod_oil_filter", "type": "product", "quantity": 3}
        )
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_diagnostics", "type": "service", "quantity": 1}
        )

        # Assert
        assert response.status_code == 200
        data = response.json()

        # Expected calculation:
        # svc_oil_change: 2500.00 * 2 = 5000.00
        # prod_oil_filter: 1000.00 * 3 = 3000.00
        # svc_diagnostics: 1500.00 * 1 = 1500.00
        # Total: 5000.00 + 3000.00 + 1500.00 = 9500.00
        assert data["total_price"] == 9500.00

    def test_total_price_accumulated_quantity(self, test_client: TestClient):
        """
        Test total price updates when adding same item multiple times

        Validates:
        - Repository accumulates quantity for duplicate items
        - Service recalculates total after each addition
        - Price reflects accumulated quantity
        """
        # Arrange & Act - Add same item three times
        response1 = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 1}
        )
        response2 = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 2}
        )
        response3 = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 3}
        )

        # Assert progression
        assert response1.json()["total_price"] == 2500.00  # 1 * 2500
        assert response2.json()["total_price"] == 7500.00  # 3 * 2500
        assert response3.json()["total_price"] == 15000.00  # 6 * 2500

        # Verify final state
        get_response = test_client.get("/api/cart")
        cart_data = get_response.json()
        assert len(cart_data["items"]) == 1  # Still one unique item
        assert cart_data["items"][0]["quantity"] == 6  # Total quantity
        assert cart_data["total_price"] == 15000.00

    def test_total_price_after_removal(self, test_client: TestClient):
        """
        Test total price recalculates correctly after item removal

        Validates:
        - Total price updates when items are removed
        - Service recalculates from remaining items
        """
        # Arrange - Add multiple items
        test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 1}
        )
        test_client.post(
            "/api/cart/items",
            json={"item_id": "prod_oil_filter", "type": "product", "quantity": 2}
        )

        # Initial total: 2500 + (1000 * 2) = 4500
        get_response = test_client.get("/api/cart")
        assert get_response.json()["total_price"] == 4500.00

        # Act - Remove one item
        test_client.delete("/api/cart/items/prod_oil_filter")

        # Assert - Total recalculated
        get_response = test_client.get("/api/cart")
        cart_data = get_response.json()
        # Expected: 2500.00 (only oil change remains)
        assert cart_data["total_price"] == 2500.00


class TestEmptyCartForNewUser:
    """
    Component Test 4: Test getting empty cart for new user

    Validates that the system correctly handles new users who haven't
    added any items to their cart yet.
    """

    def test_get_empty_cart_returns_empty_items_list(self, test_client: TestClient):
        """
        Test GET /api/cart for new user returns empty cart

        Validates:
        - Repository returns empty list for non-existent user_id
        - Service creates valid CartResponse with empty items
        - API returns 200 with valid structure
        """
        # Act
        response = test_client.get("/api/cart")

        # Assert
        assert response.status_code == 200
        data = response.json()

        # Verify structure
        assert "user_id" in data
        assert "items" in data
        assert "total_price" in data

        # Verify empty state
        assert isinstance(data["items"], list)
        assert len(data["items"]) == 0
        assert data["total_price"] == 0.0

    def test_empty_cart_has_valid_user_id(self, test_client: TestClient):
        """
        Test that empty cart response includes valid user_id

        Validates:
        - User ID is present even for empty cart
        - Mock authentication provides consistent user_id
        """
        # Act
        response = test_client.get("/api/cart")

        # Assert
        assert response.status_code == 200
        data = response.json()

        # Verify user_id is valid UUID format
        user_id = data["user_id"]
        assert isinstance(user_id, str)
        assert len(user_id) == 36  # UUID format: 8-4-4-4-12
        assert user_id.count("-") == 4

    def test_empty_cart_total_price_is_zero(self, test_client: TestClient):
        """
        Test that empty cart has total_price of 0.0

        Validates:
        - Service._calculate_total_price([]) returns 0.0
        - No errors when calculating total for empty list
        """
        # Act
        response = test_client.get("/api/cart")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["total_price"] == 0.0
        assert isinstance(data["total_price"], (int, float))


class TestComplexCartScenario:
    """
    Component Test 5: Test complex scenario with multiple operations

    This test validates a realistic user workflow:
    1. Start with empty cart
    2. Add multiple items from catalog
    3. Verify total price
    4. Remove one item
    5. Verify updated total price
    6. Add more items
    7. Verify final state

    This ensures all components work together correctly in real-world usage.
    """

    def test_complete_shopping_workflow(self, test_client: TestClient):
        """
        Test complete shopping workflow with multiple operations

        Validates:
        - All components work together seamlessly
        - State is maintained correctly across operations
        - Total price updates correctly after each operation
        - Repository, service, and API layer all function properly
        """
        # Step 1: Verify cart starts empty
        response = test_client.get("/api/cart")
        assert response.status_code == 200
        initial_cart = response.json()
        assert len(initial_cart["items"]) == 0
        assert initial_cart["total_price"] == 0.0

        # Step 2: Add oil change service (1x @ 2500.00)
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 1}
        )
        assert response.status_code == 200
        cart_after_step2 = response.json()
        assert len(cart_after_step2["items"]) == 1
        assert cart_after_step2["total_price"] == 2500.00

        # Step 3: Add oil filter product (3x @ 1000.00)
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "prod_oil_filter", "type": "product", "quantity": 3}
        )
        assert response.status_code == 200
        cart_after_step3 = response.json()
        assert len(cart_after_step3["items"]) == 2
        # Expected: 2500 + (1000 * 3) = 5500
        assert cart_after_step3["total_price"] == 5500.00

        # Step 4: Add diagnostics service (2x @ 1500.00)
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_diagnostics", "type": "service", "quantity": 2}
        )
        assert response.status_code == 200
        cart_after_step4 = response.json()
        assert len(cart_after_step4["items"]) == 3
        # Expected: 2500 + 3000 + 3000 = 8500
        assert cart_after_step4["total_price"] == 8500.00

        # Step 5: Verify cart state via GET
        response = test_client.get("/api/cart")
        assert response.status_code == 200
        cart_state = response.json()
        assert len(cart_state["items"]) == 3
        assert cart_state["total_price"] == 8500.00

        # Verify individual items
        item_map = {item["item_id"]: item for item in cart_state["items"]}
        assert item_map["svc_oil_change"]["quantity"] == 1
        assert item_map["prod_oil_filter"]["quantity"] == 3
        assert item_map["svc_diagnostics"]["quantity"] == 2

        # Step 6: Remove oil filter product
        response = test_client.delete("/api/cart/items/prod_oil_filter")
        assert response.status_code == 204

        # Step 7: Verify cart updated after removal
        response = test_client.get("/api/cart")
        assert response.status_code == 200
        cart_after_removal = response.json()
        assert len(cart_after_removal["items"]) == 2
        # Expected: 2500 + 3000 = 5500
        assert cart_after_removal["total_price"] == 5500.00

        # Verify removed item is gone
        item_ids = {item["item_id"] for item in cart_after_removal["items"]}
        assert "svc_oil_change" in item_ids
        assert "svc_diagnostics" in item_ids
        assert "prod_oil_filter" not in item_ids

        # Step 8: Add oil change service again (should accumulate)
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 2}
        )
        assert response.status_code == 200
        cart_after_add = response.json()
        assert len(cart_after_add["items"]) == 2  # Still 2 unique items
        # Expected: (2500 * 3) + 3000 = 10500
        assert cart_after_add["total_price"] == 10500.00

        # Verify oil change quantity accumulated
        oil_change_item = next(
            item for item in cart_after_add["items"]
            if item["item_id"] == "svc_oil_change"
        )
        assert oil_change_item["quantity"] == 3  # 1 + 2

        # Step 9: Final verification - get cart one more time
        response = test_client.get("/api/cart")
        assert response.status_code == 200
        final_cart = response.json()
        assert len(final_cart["items"]) == 2
        assert final_cart["total_price"] == 10500.00

        # Verify final quantities
        final_item_map = {item["item_id"]: item for item in final_cart["items"]}
        assert final_item_map["svc_oil_change"]["quantity"] == 3
        assert final_item_map["svc_diagnostics"]["quantity"] == 2

    def test_error_recovery_preserves_cart_state(self, test_client: TestClient):
        """
        Test that failed operations don't corrupt cart state

        Validates:
        - Cart state is preserved after validation errors
        - Repository rollback/transaction semantics work correctly
        - System remains consistent after errors
        """
        # Step 1: Add valid items
        test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "service", "quantity": 1}
        )
        test_client.post(
            "/api/cart/items",
            json={"item_id": "prod_oil_filter", "type": "product", "quantity": 2}
        )

        # Verify initial state
        response = test_client.get("/api/cart")
        initial_state = response.json()
        assert len(initial_state["items"]) == 2
        assert initial_state["total_price"] == 4500.00

        # Step 2: Try to add invalid item (should fail)
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "invalid_item", "type": "service", "quantity": 1}
        )
        assert response.status_code == 404

        # Step 3: Verify cart unchanged
        response = test_client.get("/api/cart")
        after_error_state = response.json()
        assert len(after_error_state["items"]) == 2
        assert after_error_state["total_price"] == 4500.00

        # Step 4: Try to add item with wrong type (should fail)
        response = test_client.post(
            "/api/cart/items",
            json={"item_id": "svc_oil_change", "type": "product", "quantity": 1}
        )
        assert response.status_code == 400

        # Step 5: Verify cart still unchanged
        response = test_client.get("/api/cart")
        final_state = response.json()
        assert len(final_state["items"]) == 2
        assert final_state["total_price"] == 4500.00

        # Verify exact same items
        assert initial_state["items"] == final_state["items"]
