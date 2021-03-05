# Framework edi_base

This module provides a framework for integrations between Odoo and other information systems.  
3 main concepts are represented, corresponding to 3 questions I have to anwser when developing a data exchange between
systems :

- **What** data to exchange : an integration
- **How** exchange the data : a connection
- **When** the data are sent : a synchronization

## The 3 concepts

### Connection

The connection allows to handle the communication between Odoo & the external software to :
- connect
- send or receive the data
- disconnect

### Synchronization

Synchronizations are just a log information.
Each time the cron task related to the integration tried to connect & send or retrieve data, a synchronization related 
to the same integration was logged, with some useful information (ok, error, error message,...)

### Integration

This data model is the main concept of integrations.

It allows configuring :

- **the flow :** out (from Odoo to xx) / in (from xx to Odoo)
- **the content type :** XML, JSON,...
- **a filter :** a link to an ir.filter, allowing to target some specific records of a data model that has to be sent  
  (so basically in an "out" flow I can for ex. target all products with a specific domain, if I want to send these)
- **cron information :** an integration inherits from a ir.cron, so I can define the frequency of the synchronizations
- **a connection :** a link to a connection record
- **some synchronizations :** link to synchronizations

## How to start a new integration

2 things to add in a new or existing module :
- XML data to create a new record for a connection, an integration, and an ir.filter (if "out" flow)
- Python class that inherits an integration & redefine 

### Example "out" flow :
```
<data>
    <record id="send_products_to_xx_software_filter" model="ir.filters">
        <field name="name">Products to send to xx software</field>
        <field name="model_id">product.template</field>
        <field name="domain">[["type","=","product"]]</field>
    </record>
</data>
<data noupdate="1">
  <record id="send_products_to_xx_software_connection" model="edi.connection">
      <field name="name">xx software Products</field>
      <field name="type">ftp</field>
      <field name="configuration"><![CDATA[
{
"host": "localhost",
"user": "myuser",
"password": "mypassword",
"out_folder": "/home/ftp/my_project/xx_software/products/out"
}
    ]]></field>
  </record>
  
  <record id="send_products_to_xx_software_integration" model="edi.integration">
      <field name="name">Send Products to xx software</field>
      <field name="type">send_products_to_xx_software</field>
      <field name="integration_flow">out</field>
      <field name="interval_number">1</field>
      <field name="interval_type">days</field>
      <field name="synchronization_content_type">xml</field>
      <field name="synchronization_creation">one</field> <!-- 1 XML file per product  -->
      <field name="connection_id" ref="send_products_to_xx_software"/>
      <field name="record_filter_id" ref="send_products_to_xx_software_filter"/>
      <field name="active" eval="False" /> <!-- archived by default to avoid automatic cron execution during the dev !-->
  </record>
</data>
```

```
class SendProducts(models.Model):
    _inherit = 'edi.integration'

    type = fields.Selection(
        selection_add=[('send_products_to_xx_software', 'Send Products to xx software')],
        ondelete={'send_products_to_xx_software': 'cascade'})

    def _get_content(self, records):
        if self.type != 'send_products_to_xx_software':
            return super()._get_content(records)

        # Generate a string containing XML data
```

### Example "in" flow :
```
<data noupdate="1">
  <record id="get_products_from_xx_software_connection" model="edi.connection">
      <field name="name">xx software Products</field>
      <field name="type">ftp</field>
      <field name="configuration"><![CDATA[
{
"host": "localhost",
"user": "myuser",
"password": "mypassword",
"in_folder": "/home/ftp/my_project/xx_software/products/in",
"in_folder_done": "/home/ftp/my_project/xx_software/products/in/done",
"in_folder_error": "/home/ftp/my_project/xx_software/products/in/error"
}
    ]]></field>
  </record>
  
  <record id="get_products_from_xx_software_integration" model="edi.integration">
      <field name="name">Get Products from xx software</field>
      <field name="type">get_products_from_xx_software</field>
      <field name="integration_flow">in</field>
      <field name="interval_number">1</field>
      <field name="interval_type">days</field>
      <field name="synchronization_content_type">xml</field>
      <field name="connection_id" ref="get_products_from_xx_software_connection"/>
      <field name="active" eval="False" /> <!-- archived by default to avoid automatic cron execution during the dev !-->
  </record>
</data>
```

```
class GetProducts(models.Model):
    _inherit = 'edi.integration'

    type = fields.Selection(
        selection_add=[('get_products_from_xx_software', 'Get Products from xx software')],
        ondelete={'get_products_from_xx_software': 'cascade'})

    def _process_content(self, filename, content):
        if self.type != 'get_products_from_xx_software':
            return super()._process_content(filename, content)

        # Process the content
        
        return 'done'
```

## More development info

### Connection

Do I use one ? Do I create one ?

#### Create a new type of connection

For example if I need to connect through an API then I need to define a new type of connection and handle the
communication as described by the API documentation of the external tool.
The data model Connection provides a bunch of methods that I can redefine to take care of that.

#### Or use an existing one

If I need to exchange data through FTP or SFTP, then 2 modules edi_ftp_connection and edi_sftp_connection have already
been developed. I just have to depend on these modules to be able to use their connections.

Then all I have to configure is the details of the connection (url / login / password,...)  
See these modules for more information.

### Integration

The data model Integration provides a bunch of methods that can be redefined.  
Most methods have a default behaviour, and just 2 **must** be redefined :

#### For an "out" flow : __process_record_out(self, records):

This method takes some records as input (basically the ones from the ir.filter defined), and the purpose is to convert
the records to something else, like a JSON or XML data.  
A string with the converted data can be returned by the method. 

#### For an "in" flow : _process_content(self, filename, content):

This method takes a filename & a content as input.  
The content is for example a string representing a JSON or XML data.  
The purpose is to convert this data, and impact the database (by create, update,...)

*new since version 1.0 :*
- **In Process Type :**  
  A field "In Process Type" allows choosing to get directly the content of the file, or to let it empty, so the file has 
  to be opened inside the method (thanks to the 1st parameter).  
  Why ?  
  Because when I have huge files I prefer to choose a different way to process the file, with a stream parser
  for example.
- **Tar files :**  
  Integrations are now able to uncompress tar files & pass each file of the tar into the method _process_content()

#### Other methods can be redefined & usage
- **_get_synchronization_name_out :**  
  set a specific name for a file to send
- **_postprocess :**  
  make something special at the end of an "out" process (change a status of a processed record,...)
- **_clean :**  
  make something special at the end of an "in" process
  
#### Other useful options

- **type of an integration :**  
  The type field is required, and corresponds to a unique name for an integration.
  As many integrations can define or redefine methods with a same name, it allows to be sure that I execute a method
  only for a specific type. So a check on the type is a required security when I define a method on an integration.
  For example : 
  ```
  def _process_content(self, filename, content):
        if self.type != 'get_products_from_xx_software':
            return super()._process_content(filename, content)
        # then I can write my code
  ```
- **synchronization_creation  (one/multi) for "out" flows :**  
  Allows to create one file per record, or one file with all records.  
  So if I have 10 products to send, "one" will allow to send 10 files with 1 product,
  and "multi" will allow to send 1 file with 10 products.
- **method  _process_out_realtime() for "out" flows :**  
  By default the processed is started by a cron task. But sometimes I want to deliver something immediately
  (through an action called from a button for ex.).  
  In this case my action can call the method _process_out_realtime(), which will also call the same method 
  _get_content(), but also take care of the SQL transaction.

#### Want to go deeper ?

Then have a look at the code, it's quite well documented.
