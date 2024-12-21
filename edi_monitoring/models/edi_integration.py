import logging

from odoo import _, fields, models


_logger = logging.getLogger(__name__)


class Integration(models.Model):
    """
    Add features allowing to monitoring the integration state, configuration, processing, ...
    in order to facilitate the bug analysis.

    - Add the ability to select different level of verbosity for logging (during a synchronization, ...)

        E.g:
        self.log("GET RECORD TO SEND")
        self.log("GET RECORD TO SEND", verbosity='high')
        self.log("GET RECORD TO SEND", level=logging.WARNING)
        self.log("GET RECORD TO SEND", level=logging.ERROR, verbosity='middle')

    - Add a chatter and tracked fields to now when a state has changed and who has modified
    the configuration.
    """

    _name = "edi.integration"
    _inherit = ["edi.integration"]

    # logging
    logging_verbosity = fields.Selection(
        selection=[("low", "Low"), ("middle", "Middle"), ("high", "High")],
        default="low",
        help="Choose the logging verbosity when performing synchronizations in Odoo logs. Used for debugging.",
        tracking=True,
    )

    def log(self, msg, *args, level=logging.INFO, verbosity="low", prefix=True, **kwargs):
        self.ensure_one()
        levels = [False] + [level for level, _ in self._fields["logging_verbosity"].selection]
        if verbosity not in levels:
            raise ValueError(_("The specified logging verbosity %s does not exist", verbosity))
        if levels.index(verbosity) <= levels.index(self.logging_verbosity):
            if prefix:
                msg = "%s : %s" % (self.name, msg)
            _logger.log(level, msg, *args, **kwargs)

    def _process_in_file(self, data, raise_error=False):
        self.env.fail_safe.env.context = {**self.env.fail_safe.env.context, "file": data.get("file")}
        return super(Integration, self)._process_in_file(data, raise_error)
