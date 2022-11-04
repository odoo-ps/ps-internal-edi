
=============
EDI Framework
=============

This is the almost-perfect ecosystem to handle API/FTP/SFTP integrations.

Contribution to the repo
========================

Your inputs are truly welcome, please just try to follow those rules :

- install & activate pre-commit for this repo
- create a dev branch then a PR
- respect the conventions about branch naming, PR description & commit
- check that the branch is green
- ask mgrenson or sct-odoo to review it

& don't hesitate to discuss **first** with mgrenson or sct-odoo about the change you would like to do ;)

Thanks !

**To install pre-commit :**
::
    pip install pre-commit
    pre-commit install (inside local repo of ps-internal-edi)

It's split in several modules :

Documentation
=============

Base
----

- `edi_base <edi_base/README.rst>`_

Additional connectors
---------------------

- edi_ftp_connection
- edi_sftp_connection

Additional features
-------------------

- `edi_archiving <edi_archiving/README.rst>`_
- `edi_monitoring <edi_monitoring/README.rst>`_

Tests
-----

- test_edi_base
