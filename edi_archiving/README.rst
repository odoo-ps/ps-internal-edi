======================================
Automatically archive synchronizations
======================================

- add active field on synchronizations & synchronizations_errors
- allow to choose duration before archive synchronizations, by status
- let internal odoo vacuum archive them depending on the configuration
- empty the content sent or received at the same time
  (to limit the DB from growing infinitely, considering that it could have been useful for debug,
  but once we decide to archive we agree to loose that information)

When archiving, only 10.000 records are considered at the same time.
If there is more than than (initial usage for ex), the execution is delegated to the next execution the minute after.
