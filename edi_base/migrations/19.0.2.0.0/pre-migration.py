from odoo.upgrade.util import column_exists, create_column


def migrate(cr, version):

    # --- edi_synchronization: replace content with received_content or sent_content ---

    if not column_exists(cr, "edi_synchronization", "received_content"):
        create_column(cr, "edi_synchronization", "received_content", "text")
        # IN: move content (data received) → received_content
        cr.execute(
            """
            UPDATE edi_synchronization
            SET received_content = content
            WHERE synchronization_flow = 'in' AND content IS NOT NULL
        """
        )

    if not column_exists(cr, "edi_synchronization", "sent_content"):
        create_column(cr, "edi_synchronization", "sent_content", "text")
        # OUT: content (payload sent) → sent_content
        cr.execute(
            """
            UPDATE edi_synchronization
            SET sent_content = content
            WHERE synchronization_flow = 'out' AND content IS NOT NULL
        """
        )

    # --- edi_integration: replace write_content_on_sync with store_received_content + store_sent_content ---

    if not column_exists(cr, "edi_integration", "store_received_content"):
        create_column(cr, "edi_integration", "store_received_content", "boolean")
        cr.execute(
            """
            UPDATE edi_integration
            SET store_received_content = COALESCE(write_content_on_sync, TRUE)
        """
        )

    if not column_exists(cr, "edi_integration", "store_sent_content"):
        create_column(cr, "edi_integration", "store_sent_content", "boolean")
        cr.execute(
            """
            UPDATE edi_integration
            SET store_sent_content = COALESCE(write_content_on_sync, TRUE)
        """
        )
