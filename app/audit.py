"""Read-only inventory reconciliation and operational health calculations."""
import sqlite3
import time


def reconcile(db: sqlite3.Connection, now: int | None = None) -> dict:
    """Report per-SKU physical stock, active holds, available units and violations.

    Caller owns the transaction; expired holds are ignored without mutation.
    """
    now = int(time.time()) if now is None else now
    rows = db.execute(
        """SELECT i.sku, i.stock,
                  COALESCE(SUM(CASE WHEN r.status = 'active' AND r.expires_at > ?
                                    THEN r.quantity ELSE 0 END), 0) AS held
           FROM inventory AS i LEFT JOIN reservations AS r ON r.sku = i.sku
           GROUP BY i.sku, i.stock ORDER BY i.sku""", (now,)
    ).fetchall()
    items = []
    for sku, stock, held in rows:
        items.append({'sku': sku, 'stock': stock, 'held': held,
                      'available': stock - held, 'oversubscribed': held > stock})
    return {'items': items, 'sku_count': len(items),
            'oversubscribed_count': sum(item['oversubscribed'] for item in items)}
