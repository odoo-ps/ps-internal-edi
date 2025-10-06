from odoo.upgrade.util import column_exists, create_column


def migrate(cr, version):
    if not column_exists(cr, "edi_integration", "last_state"):
        create_column(cr, "edi_integration", "last_state", "character varying")
        cr.execute(
            """
            UPDATE edi_integration
            SET last_state = CASE last_sync_status
                WHEN 'Success' THEN 'done'
                WHEN 'Fail' THEN 'fail'
                WHEN 'No Sync Yet' THEN 'no_sync'
            END
        """
        )
