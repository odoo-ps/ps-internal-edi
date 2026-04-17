==============================================
Automatically archive EDI 2-steps queue records
==============================================
- add an active field on EDI 2-steps queue records (`edi.table.record`)
- allow choosing how long to keep queue records before archiving them, by status
- let Odoo's internal vacuum archive them depending on the configuration
- empty the sent or received content at the same time
  (to limit DB growth, considering that this content can be useful for debugging,
  but once we decide to archive the record we agree to lose that information)
When archiving, only 10.000 records are considered at the same time.
If there are more than that (for example on initial usage), processing is delegated to the next execution the minute after.
