ALTER TABLE public.inventory_transactions
    ALTER COLUMN delta TYPE numeric(20, 6) USING delta::numeric;

ALTER TABLE public.procurement_items
    ALTER COLUMN quantity TYPE numeric(20, 6) USING quantity::numeric;

ALTER TABLE public.inventory_transactions
    ADD COLUMN garden_id bigint;

UPDATE public.inventory_transactions AS tx
SET garden_id = item.garden_id
FROM public.inventory_items AS item
WHERE item.id = tx.item_id
  AND tx.garden_id IS NULL;

ALTER TABLE public.inventory_transactions
    ALTER COLUMN garden_id SET NOT NULL;

ALTER TABLE public.inventory_items
    ADD CONSTRAINT ux_inventory_items_id_garden UNIQUE (id, garden_id);

ALTER TABLE public.inventory_transactions
    ADD CONSTRAINT ux_inventory_tx_id_item_garden UNIQUE (id, item_id, garden_id),
    ADD CONSTRAINT fk_inventory_tx_garden
        FOREIGN KEY (garden_id) REFERENCES public.gardens(id)
        ON DELETE CASCADE DEFERRABLE,
    ADD CONSTRAINT fk_inventory_tx_item_garden
        FOREIGN KEY (item_id, garden_id)
        REFERENCES public.inventory_items(id, garden_id)
        ON DELETE CASCADE DEFERRABLE,
    ADD CONSTRAINT ck_inventory_tx_delta_nonzero CHECK (delta <> 0) NOT VALID;

ALTER TABLE public.procurement_items
    ADD COLUMN receipt_inventory_item_id bigint,
    ADD COLUMN receipt_inventory_transaction_id bigint,
    ADD COLUMN received_by_user_id bigint,
    ADD COLUMN received_at_ms bigint;

WITH receipt_metadata AS (
    SELECT
        procurement.id AS procurement_id,
        CASE
            WHEN pg_input_is_valid(procurement.metadata_json, 'jsonb')
                THEN procurement.metadata_json::jsonb
            ELSE '{}'::jsonb
        END AS metadata
    FROM public.procurement_items AS procurement
), resolved_receipts AS (
    SELECT DISTINCT ON (procurement.id)
        procurement.id AS procurement_id,
        item.id AS inventory_item_id,
        tx.id AS inventory_transaction_id,
        tx.actor_user_id,
        tx.created_at_ms
    FROM public.procurement_items AS procurement
    JOIN receipt_metadata AS receipt
      ON receipt.procurement_id = procurement.id
    JOIN public.inventory_items AS item
      ON item.garden_id = procurement.garden_id
     AND (
         item.public_id = receipt.metadata ->> 'inventory_item_id'
         OR item.id::text = receipt.metadata ->> 'inventory_item_id'
     )
    JOIN public.inventory_transactions AS tx
      ON tx.id::text = receipt.metadata ->> 'inventory_transaction_id'
     AND tx.item_id = item.id
     AND tx.garden_id = procurement.garden_id
    WHERE procurement.status = 'received'
    ORDER BY procurement.id, tx.id
)
UPDATE public.procurement_items AS procurement
SET receipt_inventory_item_id = receipt.inventory_item_id,
    receipt_inventory_transaction_id = receipt.inventory_transaction_id,
    received_by_user_id = receipt.actor_user_id,
    received_at_ms = receipt.created_at_ms
FROM resolved_receipts AS receipt
WHERE receipt.procurement_id = procurement.id;

ALTER TABLE public.procurement_items
    ADD CONSTRAINT ux_procurement_receipt_transaction
        UNIQUE (receipt_inventory_transaction_id),
    ADD CONSTRAINT fk_procurement_receipt_inventory
        FOREIGN KEY (
            receipt_inventory_transaction_id,
            receipt_inventory_item_id,
            garden_id
        ) REFERENCES public.inventory_transactions(id, item_id, garden_id)
        DEFERRABLE,
    ADD CONSTRAINT fk_procurement_received_by_user
        FOREIGN KEY (received_by_user_id) REFERENCES public.auth_users(id)
        ON DELETE SET NULL DEFERRABLE,
    ADD CONSTRAINT ck_procurement_receipt_provenance
        CHECK (
            (
                receipt_inventory_item_id IS NULL
                AND receipt_inventory_transaction_id IS NULL
                AND received_at_ms IS NULL
            )
            OR (
                receipt_inventory_item_id IS NOT NULL
                AND receipt_inventory_transaction_id IS NOT NULL
                AND received_at_ms IS NOT NULL
                AND status = 'received'
            )
        ),
    ADD CONSTRAINT ck_procurement_quantity_positive CHECK (quantity > 0) NOT VALID;
