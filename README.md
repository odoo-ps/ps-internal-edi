# psbe-internal

Repo to store modules useful for many projects.

## Installation of modules
In order not to install all the modules in the repo for your project, you can add the submodule in a folder preceeded by `.`, this way Odoo.sh ignores the content of this folder and it is not installed in your dev branches.
```
git submodule add -b <your_target_branch> git@github.com:odoo-ps/psbe-internal.git .psbe-internal
```
Then you can create a symbolic link in the root folder of your project to the module(s) that you are interested on:
```
ln -s .psbe-internal/<target_module> <target_module>/
```
This way Odoo.sh only install the modules you are interested on and not the whole repository.

## Requirements
Not all modules require all the packages in `requirements.txt`, they are added at the root folder so they are installed by Odoo.sh when we are testing the modules.

When you are installing only some modules for your project, manual handling is advised.

The following modules require some packages:
- `auth_oath_azure`
  - `cryptography`
  - `pyjwt`
- `edi_sftp_connection`
  - `pysftp`
- `export_diff_dump`
  - `pysftp`

You should create you own `requirements.txt` in the root folder of your project, with the adequate requirements for the modules you need.
