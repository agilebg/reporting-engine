from base64 import b64decode
import json
from tempfile import NamedTemporaryFile
from cStringIO import StringIO
from py3o.template.helpers import Py3oConvertor

import requests
from py3o.template import Template

from openerp.report.report_sxw import report_sxw
from openerp.osv import osv
from openerp import pooler
from openerp.tools.translate import _


class py3o_report(report_sxw):
    # def __init__(self, name, table):
    #         super(py3o_report, self).__init__(name, table)

    def get_values(self, cr, uid, ids, data, context):
        """ Override this function to customize the dictionary given to the
        py3o.template renderer. """

        return {
            'lang': self.get_lang(cr, uid, context),
            'objects': self.getObjects(cr, uid, ids, context),
        }

    @staticmethod
    def get_lang(cr, uid, context):
        pool = pooler.get_pool(cr.dbname)
        lang_obj = pool.get('res.lang')
        user_obj = pool.get('res.users')

        lang_code = user_obj.browse(cr, uid, uid, context=context).lang
        lang = lang_obj.search(cr, uid,
                               [('code', '=', lang_code)],
                               context=context)[0]
        return lang_obj.browse(cr, uid, lang, context=context)

    @staticmethod
    def format_date(date, values):
        """ Return a date formatted according to the language extracted from
        the "values" argument (which should be the result of get_values). """
        return date.strftime(values['lang'].date_format)

    def create(self, cr, uid, ids, data, context=None):
        # Find the report definition to get its settings.
        pool = pooler.get_pool(cr.dbname)
        report_xml_obj = pool.get('ir.actions.report.xml')
        report_xml_ids = report_xml_obj.search(
            cr, uid,
            [('report_name', '=', self.name[7:])],  # Ignore "report."
            context=context,
        )
        if not report_xml_ids:
            return super(py3o_report, self).create(cr, uid, ids, data,
                                                   context=context)
        report_xml = report_xml_obj.browse(cr, uid,
                                           report_xml_ids[0],
                                           context=context)

        #TODO: Move this stuff in py3o.template as a helper function
        tmpl_def = report_xml.py3o_template_id
        in_stream = StringIO(b64decode(tmpl_def.py3o_template_data))
        out_stream = StringIO()
        template = Template(in_stream, out_stream)
        expressions = template.get_all_user_python_expression()
        py_expression = template.convert_py3o_to_python_ast(expressions)
        convertor = Py3oConvertor()
        data_struct = convertor(py_expression)

        filetype = report_xml.py3o_fusion_filetype

        datadict = self.get_values(cr, uid, ids, data, context)

        res = data_struct.render(datadict)

        fusion_server_obj = pool.get('py3o.server')
        fusion_server_id = fusion_server_obj.search(
            cr, uid, [], context=context
        )[0]
        fusion_server = fusion_server_obj.browse(
            cr, uid, fusion_server_id, context=context
        )
        in_stream.seek(0)
        files = {
            'tmpl_file': in_stream,
        }
        fields = {
            "targetformat": filetype.fusion_ext,
            "datadict": json.dumps(res),
            "image_mapping": "{}",
        }
        # Here is a little joke about Odoo
        # we do nice chunked reading from the network...
        r = requests.post(fusion_server.url, data=fields, files=files)
        if r.status_code == 400:
            # server says we have an issue... let's tell that to enduser
            raise osv.except_osv(
                _('Fusion server error'),
                r.json(),
            )

        chunk_size = 1024
        with NamedTemporaryFile(
            suffix=filetype.human_ext,
            prefix='py3o-template-'
        ) as fd:
            for chunk in r.iter_content(chunk_size):
                fd.write(chunk)
            fd.seek(0)
            # ... but odoo wants the whole data in memory anyways :)
            return fd.read(), filetype.human_ext
