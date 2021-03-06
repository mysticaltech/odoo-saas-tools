import os
import openerp
from ast import literal_eval
from openerp import SUPERUSER_ID
from openerp import models, fields
from openerp.tools import config
from openerp.addons.saas_utils import connector, database

import logging
_logger = logging.getLogger(__name__)


def get_size(start_path='.'):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(start_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return total_size


class SaasServerPlan(models.Model):
    _name = 'saas_server.plan'

    name = fields.Char('Plan')
    template = fields.Char('Template')
    demo = fields.Boolean('Demo Data')
    sequence = fields.Integer('Sequence')
    state = fields.Selection([('draft', 'Draft'), ('confirmed', 'Confirmed')],
                             'State', default='draft')
    role_id = fields.Many2one('saas_server.role', 'Role')
    required_addons_ids = fields.Many2many('ir.module.module',
                                           rel='company_required_addons_rel',
                                           id1='company_id', id2='module_id',
                                           string='Required Addons')
    optional_addons_ids = fields.Many2many('ir.module.module',
                                           rel='company_optional_addons_rel',
                                           id1='company_id', id2='module_id',
                                           string='Optional Addons')
    client_ids = fields.One2many('saas_server.client', 'plan_id', 'Clients')
    
    automatic_tenant = fields.Boolean('Automatic Tenant', default=True)
    redirect_url = fields.Char('Redirect URL')
    template_user = fields.Many2one('res.users', 'Template User')

    _order = 'sequence'

    def create_template(self, cr, uid, ids, context=None):
        obj = self.browse(cr, uid, ids[0])
        openerp.service.db.exp_create_database(obj.template, obj.demo, 'en_US')
        addon_names = [x.name for x in obj.required_addons_ids]
        if 'saas_client' not in addon_names:
            addon_names.append('saas_client')
        to_search = [('name', 'in', addon_names)]
        addon_ids = connector.call(obj.template, 'ir.module.module',
                                   'search', to_search)
        for addon_id in addon_ids:
            connector.call(obj.template, 'ir.module.module',
                           'button_immediate_install', addon_id)
        return self.write(cr, uid, obj.id, {'state': 'confirmed'})

    def edit_template(self, cr, uid, ids, context=None):
        obj = self.browse(cr, uid, ids[0])
        d = config.get('local_url')
        url = '%s/login?db=%s&login=admin&key=admin' % (d, obj.template)
        return {
            'type': 'ir.actions.act_url',
            'target': 'self',
            'name': 'Edit Template',
            'url': url
        }

    def upgrade_template(self, cr, uid, ids, context=None):
        obj = self.browse(cr, uid, ids[0])
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'saas.config',
            'target': 'new',
            'context': {
                'default_action': 'upgrade',
                'default_database': obj.template
            }
        }

    def delete_template(self, cr, uid, ids, context=None):
        obj = self.browse(cr, uid, ids[0])
        openerp.service.db.exp_drop(obj.template)
        return self.write(cr, uid, obj.id, {'state': 'draft'})


class SaasServerRole(models.Model):
    _name = 'saas_server.role'

    name = fields.Char('Name', size=64)
    code = fields.Char('Code', size=64)


class SaasServerClient(models.Model):
    _name = 'saas_server.client'

    name = fields.Char('Database name', readonly=True)
    client_id = fields.Char('Client ID', readonly=True, select=True)
    users_len = fields.Integer('Count users')
    file_storage = fields.Integer('File storage (MB)')
    db_storage = fields.Integer('DB storage (MB)')
    plan_id = fields.Many2one('saas_server.plan', 'Plan')

    def update_all(self, cr, uid, server_db):
        db_list = database.get_market_dbs(with_templates=False)
        _logger.info("Bases de datos: %s", str(db_list))
        try:
            client_list.remove(server_db)
        except:
            pass

        res = []
        for db in db_list:
            registry = openerp.modules.registry.RegistryManager.get(db)
            with registry.cursor() as db_cr:
                client_id = registry['ir.config_parameter'].get_param(db_cr,
                                                SUPERUSER_ID, 'database.uuid')
                users = registry['res.users'].search(db_cr, SUPERUSER_ID,
                                                     [('share', '=', False)])
                users_len = len(users)
                data_dir = openerp.tools.config['data_dir']

                file_storage = get_size('%s/filestore/%s' % (data_dir, db))
                file_storage = int(file_storage / (1024 * 1024))

                db_cr.execute("select pg_database_size('%s')" % db)
                db_storage = db_cr.fetchone()[0]
                db_storage = int(db_storage / (1024 * 1024))

                data = {
                    'name': db,
                    'client_id': client_id,
                    'users_len': users_len,
                    'file_storage': file_storage,
                    'db_storage': db_storage,
                }
                oid = self.search(cr, uid, [('client_id', '=', client_id)])
                if not oid:
                    self.create(cr, uid, data)
                else:
                    self.write(cr, uid, oid, data)
                res.append(data)

        return res


class ResUsers(models.Model):
    _name = 'res.users'
    _inherit = 'res.users'

    plan_id = fields.Many2one('saas_server.plan', 'Plan')
    organization = fields.Char('Organization', size=64)
    database = fields.Char('Database', size=64)
    subdomain = fields.Char('Subdomain')
    
    def _signup_create_user(self, cr, uid, values, context=None):
        ir_config_parameter = self.pool.get('ir.config_parameter')
        
        """ create a new user from the template user """
        template_user_id = False
        if values.get('plan_id'):
            plan = self.pool.get('saas_server.plan').browse(cr, uid, int(values['plan_id']))
            template_user_id = plan.template_user and plan.template_user.id or False

        if not template_user_id:
            template_user_id = literal_eval(ir_config_parameter.get_param(cr, uid, 'auth_signup.template_user_id', 'False'))
            assert template_user_id and self.exists(cr, uid, template_user_id, context=context), 'Signup: invalid template user'

        # check that uninvited users may sign up
        if 'partner_id' not in values:
            if not literal_eval(ir_config_parameter.get_param(cr, uid, 'auth_signup.allow_uninvited', 'False')):
                raise SignupError('Signup is not allowed for uninvited users')

        assert values.get('login'), "Signup: no login given for new user"
        assert values.get('partner_id') or values.get('name'), "Signup: no name or partner given for new user"

        # create a copy of the template user (attached to a specific partner_id if given)
        values['active'] = True
        context = dict(context or {}, no_reset_password=True)
        try:
            with cr.savepoint():
                return self.copy(cr, uid, template_user_id, values, context=context)
        except Exception, e:
            # copy may failed if asked login is not available.
            raise SignupError(ustr(e))
