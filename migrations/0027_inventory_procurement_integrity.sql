ALTER TABLE public.inventory_transactions
    ALTER COLUMN delta TYPE numeric(20, 6) USING delta::numeric;

ALTER TABLE public.procurement_items
    ALTER COLUMN quantity TYPE numeric(20, 6) USING quantity::numeric;

ALTER TABLE public.inventory_transactions
    ADD COLUMN IF NOT EXISTS garden_id bigint;

UPDATE public.inventory_transactions AS tx
SET garden_id = item.garden_id
FROM public.inventory_items AS item
WHERE item.id = tx.item_id
  AND tx.garden_id IS NULL;

ALTER TABLE public.inventory_transactions
    ALTER COLUMN garden_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ux_inventory_items_id_garden'
          AND conrelid = 'public.inventory_items'::regclass
    ) THEN
        ALTER TABLE public.inventory_items
            ADD CONSTRAINT ux_inventory_items_id_garden UNIQUE (id, garden_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ux_inventory_tx_id_item_garden'
          AND conrelid = 'public.inventory_transactions'::regclass
    ) THEN
        ALTER TABLE public.inventory_transactions
            ADD CONSTRAINT ux_inventory_tx_id_item_garden UNIQUE (id, item_id, garden_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_inventory_tx_garden'
          AND conrelid = 'public.inventory_transactions'::regclass
    ) THEN
        ALTER TABLE public.inventory_transactions
            ADD CONSTRAINT fk_inventory_tx_garden
            FOREIGN KEY (garden_id) REFERENCES public.gardens(id)
            ON DELETE CASCADE DEFERRABLE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_inventory_tx_item_garden'
          AND conrelid = 'public.inventory_transactions'::regclass
    ) THEN
        ALTER TABLE public.inventory_transactions
            ADD CONSTRAINT fk_inventory_tx_item_garden
            FOREIGN KEY (item_id, garden_id)
            REFERENCES public.inventory_items(id, garden_id)
            ON DELETE CASCADE DEFERRABLE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_inventory_tx_delta_nonzero'
          AND conrelid = 'public.inventory_transactions'::regclass
    ) THEN
        ALTER TABLE public.inventory_transactions
            ADD CONSTRAINT ck_inventory_tx_delta_nonzero CHECK (delta <> 0) NOT VALID;
    END IF;
END
$$;

ALTER TABLE public.procurement_items
    ADD COLUMN IF NOT EXISTS receipt_inventory_item_id bigint,
    ADD COLUMN IF NOT EXISTS receipt_inventory_transaction_id bigint,
    ADD COLUMN IF NOT EXISTS received_by_user_id bigint,
    ADD COLUMN IF NOT EXISTS received_at_ms bigint;

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

DO $$
DECLARE
    unresolved_count bigint;
    unresolved_ids text;
BEGIN
    SELECT count(*)
    INTO unresolved_count
    FROM public.procurement_items
    WHERE status = 'received'
      AND (
          receipt_inventory_item_id IS NULL
          OR receipt_inventory_transaction_id IS NULL
          OR received_at_ms IS NULL
      );

    IF unresolved_count > 0 THEN
        SELECT string_agg(id::text, ', ' ORDER BY id)
        INTO unresolved_ids
        FROM (
            SELECT id
            FROM public.procurement_items
            WHERE status = 'received'
              AND (
                  receipt_inventory_item_id IS NULL
                  OR receipt_inventory_transaction_id IS NULL
                  OR received_at_ms IS NULL
              )
            ORDER BY id
            LIMIT 20
        ) AS unresolved;

        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Migration 0027 cannot establish receipt provenance for legacy received procurement items.',
            DETAIL = format(
                '%s received procurement item(s) are unresolved. IDs (up to 20): %s',
                unresolved_count,
                unresolved_ids
            ),
            HINT = 'Correct metadata_json so inventory_item_id identifies an inventory item in the same garden and inventory_transaction_id identifies a transaction for that item, then rerun migration 0027.';
    END IF;
END
$$;

ALTER TABLE public.procurement_items
    DROP CONSTRAINT IF EXISTS ck_procurement_receipt_provenance;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ux_procurement_receipt_transaction'
          AND conrelid = 'public.procurement_items'::regclass
    ) THEN
        ALTER TABLE public.procurement_items
            ADD CONSTRAINT ux_procurement_receipt_transaction
            UNIQUE (receipt_inventory_transaction_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_procurement_receipt_inventory'
          AND conrelid = 'public.procurement_items'::regclass
    ) THEN
        ALTER TABLE public.procurement_items
            ADD CONSTRAINT fk_procurement_receipt_inventory
            FOREIGN KEY (
                receipt_inventory_transaction_id,
                receipt_inventory_item_id,
                garden_id
            ) REFERENCES public.inventory_transactions(id, item_id, garden_id)
            DEFERRABLE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_procurement_received_by_user'
          AND conrelid = 'public.procurement_items'::regclass
    ) THEN
        ALTER TABLE public.procurement_items
            ADD CONSTRAINT fk_procurement_received_by_user
            FOREIGN KEY (received_by_user_id) REFERENCES public.auth_users(id)
            ON DELETE SET NULL DEFERRABLE;
    END IF;
    ALTER TABLE public.procurement_items
        ADD CONSTRAINT ck_procurement_receipt_provenance
        CHECK (
            (
                status = 'received'
                AND receipt_inventory_item_id IS NOT NULL
                AND receipt_inventory_transaction_id IS NOT NULL
                AND received_at_ms IS NOT NULL
            )
            OR (
                status <> 'received'
                AND receipt_inventory_item_id IS NULL
                AND receipt_inventory_transaction_id IS NULL
                AND received_by_user_id IS NULL
                AND received_at_ms IS NULL
            )
        );
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_procurement_quantity_positive'
          AND conrelid = 'public.procurement_items'::regclass
    ) THEN
        ALTER TABLE public.procurement_items
            ADD CONSTRAINT ck_procurement_quantity_positive CHECK (quantity > 0) NOT VALID;
    END IF;
END
$$;
