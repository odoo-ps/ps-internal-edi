
=============
EDI Framework
=============

This is the almost-perfect ecosystem to handle API/FTP/SFTP integrations.

Documentation
=============

This repository is organized into several modules:

Base (including API connector)
------------------------------

- `edi_base <edi_base/README.rst>`_

Additional connectors
---------------------

- `edi_ftp_connection <edi_ftp_connection/README.rst>`_
- `edi_sftp_connection <edi_sftp_connection/README.rst>`_

Additional features
-------------------

- `edi_archiving <edi_archiving/README.rst>`_
- `edi_monitoring <edi_monitoring/README.rst>`_
- `edi_2steps <edi_2steps/README.rst>`_
- `edi_2steps_archiving <edi_2steps_archiving/README.rst>`_

Tests
-----

- `test_edi_base <test_edi_base/README.rst>`_
- `test_edi_2steps <test_edi_2steps/README.rst>`_

Contribution to the repo
========================

Your inputs are truly welcome, please just follow those rules :

- install & activate pre-commit on the cloned repository
- create a dev branch then a PR
- respect the conventions about branch naming, PR description & commit
- check that the branch is green
- ask mgrenson or poma-odoo to review it

& don't hesitate to discuss **first** with mgrenson or poma-odoo about the change you would like to do ;)

Thanks !

**To install pre-commit :**
::
    pip install pre-commit
    pre-commit install (inside local repo of ps-internal-edi)

License & Disclaimer
====================

The modules in this repository are licensed under the
`Odoo Enterprise Edition License v1.0 (OEEL-1) <https://www.odoo.com/documentation/master/legal/licenses.html>`_.

Limit
-----

This software and associated files (the "Software") can only be used (executed,
modified, executed after modifications) with a valid Odoo Enterprise
Subscription for the correct number of users.

With a valid Partnership Agreement with Odoo S.A., the above permissions
are also granted, as long as the usage is limited to a testing or development
environment.

Warranty
--------

These modules are published for reference purposes only and come with
**no support or maintenance commitment**.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
