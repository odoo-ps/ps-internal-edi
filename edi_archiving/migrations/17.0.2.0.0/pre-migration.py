def migrate(cr, version):
    """Carry the Cancelled box over to the parameter the settings now read.

    The box used to store edi.archive.state.cancel while the state is "cancelled", so it never
    selected a record. The settings field now stores edi.archive.state.cancelled; without this rename
    an installation that had ticked the box would find it unticked after the upgrade, and one with a
    duration and only that box ticked could no longer save its settings (a duration needs a state).

    Consequence, on purpose: a box that was ticked stays ticked, and from this version on it acts.
    Cancelled synchronizations past the configured duration are archived, and deleted if a delete
    duration is set -- what the box always promised.

    If both keys exist, the new one wins and the old one is dropped: the key is unique, and both say
    the same thing.
    """
    cr.execute(
        """
        UPDATE ir_config_parameter
           SET key = 'edi.archive.state.cancelled'
         WHERE key = 'edi.archive.state.cancel'
           AND NOT EXISTS (SELECT 1 FROM ir_config_parameter WHERE key = 'edi.archive.state.cancelled')
        """
    )
    cr.execute("DELETE FROM ir_config_parameter WHERE key = 'edi.archive.state.cancel'")
