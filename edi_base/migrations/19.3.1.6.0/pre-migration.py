def migrate(cr, version):
    cr.execute(
        """
        UPDATE edi_integration
        SET synchronization_content_type = 'txt' WHERE synchronization_content_type = 'text'
    """
    )
