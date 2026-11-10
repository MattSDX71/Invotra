"""Local integration tests. Run: python -m pytest -q

Uses a disposable SQLite database and a mocked email sender; never sends real email.
"""
import os
import io
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

# Must be set before importing app: the app initializes its database at import time.
TEST_DIR = tempfile.TemporaryDirectory(prefix='invotra_tests_')
os.environ['DATABASE_URL'] = f"sqlite:///{Path(TEST_DIR.name) / 'test.db'}"
os.environ['INVOTRA_ENV'] = 'testing'
os.environ['INVOTRA_SEED_DEMO'] = 'true'
os.environ['BOOTSTRAP_ADMIN_EMAIL'] = 'super@example.com'
os.environ['BOOTSTRAP_ADMIN_PASSWORD'] = 'firstloginpassword123!'
os.environ['SECRET_KEY'] = 'integration-test-secret-key-not-for-production'

import app as a


@pytest.fixture()
def env():
    a.app.config.update(TESTING=True, TEST_DISABLE_CSRF=False, MAIL_SUPPRESS_SEND=True)
    with a.app.app_context():
        a.db.session.remove()
        a.db.drop_all()
        a.ensure_seed_data()
        super_admin = a.User.query.filter_by(role='super_admin').one()
        super_admin.initial_password_reset_required = False
        company = a.Company.query.one()
        parent = a.ParentLocation.query.one()
        store = a.SubLocation.query.filter_by(name='C-Store').one()
        hall = a.SubLocation.query.filter_by(name='Dining Hall').one()
        gl = a.GLAccount.query.filter_by(code='60110020').one()
        item = a.ItemMaster(company_id=company.id, item_name='Coca Cola Cherry 20 oz',
            each_upc='000123456789', each_cost=Decimal('1.50'),
            case_upc='999999999999', case_cost=Decimal('12.00'), gl_account_id=gl.id)
        admin = a.User(full_name='Location Manager', email='manager@example.com', role='admin',
            status='active', initial_password_reset_required=False)
        admin.set_password('manager-password123!')
        counter1 = a.User(full_name='Counter One', email='counter1@example.com', role='user',
            status='active', initial_password_reset_required=False)
        counter1.set_password('counter-password123!')
        counter2 = a.User(full_name='Counter Two', email='counter2@example.com', role='user',
            status='active', initial_password_reset_required=False)
        counter2.set_password('counter-password123!')
        a.db.session.add_all([item,admin,counter1,counter2])
        a.db.session.flush()
        for user in (admin,counter1,counter2):
            a.db.session.add(a.UserSubLocationAccess(user_id=user.id,sub_location_id=store.id))
        a.db.session.commit()
        identifiers={'store':store.id,'hall':hall.id,'parent':parent.id,'company':company.id,
                     'gl':gl.id,'item':item.id,'admin':admin.id,'u1':counter1.id,'u2':counter2.id,'super':super_admin.id}
    with patch.object(a,'send_email',return_value=True) as mail:
        yield identifiers, mail
    with a.app.app_context():
        a.db.session.remove()


def client():
    return a.app.test_client()


def csrf(c):
    with c.session_transaction() as s:
        if 'csrf_token' not in s:
            s['csrf_token']='test-token'
        return s['csrf_token']


def post(c, url, data=None, **kwargs):
    data = dict(data or {})
    data.setdefault('csrf_token',csrf(c))
    return c.post(url, data=data, follow_redirects=True, **kwargs)


def login(c, email, pwd):
    c.get('/login')
    return post(c,'/login',{'email':email,'password':pwd})


def start(c, store):
    r=post(c,'/inventory/start',{'sub_location_id':str(store)})
    assert r.status_code==200
    assert '/inventory/' in r.request.path
    return int(r.request.path.split('/')[-1])


def test_login_forced_password_reset_and_profile(env):
    ids, _=env
    with a.app.app_context():
        u=a.db.session.get(a.User,ids['u1'])
        u.initial_password_reset_required=True
        a.db.session.commit()
    c=client()
    r=login(c,'counter1@example.com','counter-password123!')
    assert 'Set your password' in r.get_data(as_text=True)
    r=post(c,'/force-password-reset',{'password':'mynewpassword1234!','confirm_password':'mynewpassword1234!'})
    assert 'Start Inventory' in r.get_data(as_text=True)
    r=post(c,'/profile',{'full_name':'Counter Changed','phone':'555-1010','current_password':'mynewpassword1234!',
            'new_password':'anotherpassword123!','confirm_password':'anotherpassword123!'})
    assert r.status_code==200
    with a.app.app_context():
        u=a.db.session.get(a.User,ids['u1'])
        assert u.phone=='555-1010' and u.check_password('anotherpassword123!')


def test_two_people_independent_each_case_counts_and_report_access(env):
    ids,_=env
    x=client(); y=client()
    login(x,'counter1@example.com','counter-password123!')
    login(y,'counter2@example.com','counter-password123!')
    sx=start(x,ids['store']); sy=start(y,ids['store'])
    assert sx!=sy
    post(x,f'/inventory/{sx}/scan',{'upc':'000123456789'})
    post(x,f'/inventory/{sx}/scan',{'upc':'000123456789'})
    post(x,f'/inventory/{sx}/scan',{'upc':'999999999999'})
    post(y,f'/inventory/{sy}/scan',{'upc':'999999999999'})
    with a.app.app_context():
        a.db.session.expire_all()
        one=a.db.session.get(a.InventorySession,sx)
        two=a.db.session.get(a.InventorySession,sy)
        assert one.total_quantity==3 and one.total_value==Decimal('15.00')
        assert two.total_quantity==1 and two.total_value==Decimal('12.00')
    assert y.get(f'/inventory/{sx}').status_code==302
    assert f'#{sx}' not in y.get('/reports').get_data(as_text=True)


def test_unknown_item_popup_twice_submit_review_approval_email_and_csv(env):
    ids, mockmail=env
    counter=client(); manager=client()
    login(counter,'counter1@example.com','counter-password123!')
    login(manager,'manager@example.com','manager-password123!')
    inv_id=start(counter,ids['store'])
    r=post(counter,f'/inventory/{inv_id}/scan',{'upc':'N0-MISSING-UPC'})
    assert 'New / Unrecognized Item' in r.get_data(as_text=True)
    r=post(counter,f'/inventory/{inv_id}/unknown',{'scanned_upc':'N0-MISSING-UPC',
         'proposed_name':'Sparkling Water 12 oz Lime','proposed_gl_account_id':str(ids['gl'])})
    assert 'Sparkling Water' in r.get_data(as_text=True)
    post(counter,f'/inventory/{inv_id}/scan',{'upc':'N0-MISSING-UPC'})
    post(counter,f'/inventory/{inv_id}/scan',{'upc':'000123456789'})
    post(counter,f'/inventory/{inv_id}/submit')
    # Admin cannot approve while proposed items have no final price.
    r=post(manager,f'/admin/session/{inv_id}',{'action':'approve'})
    assert 'Review all missing items' in r.get_data(as_text=True)
    with a.app.app_context():
        unknown=a.UnknownItem.query.filter_by(session_id=inv_id).one()
        unknown_id=unknown.id
        assert a.db.session.get(a.InventorySession,inv_id).status=='submitted'
    post(manager,f'/admin/unknown/{unknown_id}',{'action':'approve',
        'item_name':'Sparkling Water 12 oz Lime','each_upc':'N0-MISSING-UPC','each_cost':'1.75',
        'case_upc':'','case_cost':'','gl_account_id':str(ids['gl'])})
    with a.app.app_context():
        inv=a.db.session.get(a.InventorySession,inv_id)
        assert inv.total_quantity==3 and inv.total_value==Decimal('5.00')
    post(manager,f'/admin/session/{inv_id}',{'action':'approve'})
    with a.app.app_context():
        assert a.db.session.get(a.InventorySession,inv_id).status=='approved'
    events=[x.kwargs.get('event') for x in mockmail.call_args_list]
    assert events.count('inventory_submitted_user')==1
    assert events.count('inventory_submitted_admin')==1
    assert events.count('inventory_approved')==1
    assert b'Sparkling Water' in manager.get(f'/inventory/{inv_id}/csv').data
    assert b'2' in manager.get(f'/inventory/{inv_id}/csv').data


def test_submitted_locked_and_approval_immutable_after_master_changes(env):
    ids,_=env
    c=client(); m=client(); su=client()
    login(c,'counter1@example.com','counter-password123!')
    login(m,'manager@example.com','manager-password123!')
    login(su,'super@example.com','firstloginpassword123!')
    sid=start(c,ids['store'])
    post(c,f'/inventory/{sid}/scan',{'upc':'000123456789'})
    post(c,f'/inventory/{sid}/submit')
    assert post(c,f'/inventory/{sid}/scan',{'upc':'000123456789'}).status_code==403
    post(m,f'/admin/session/{sid}',{'action':'approve'})
    with a.app.app_context():
        i=a.db.session.get(a.ItemMaster,ids['item']); i.each_cost=Decimal('77.77'); a.db.session.commit()
        assert a.db.session.get(a.InventorySession,sid).total_value==Decimal('1.50')
    assert post(m,f'/admin/session/{sid}',{'action':'approve'}).status_code==200 # locked; no status change


def test_super_admin_removes_user_preserves_audit_and_gl_deactivation(env):
    ids,_=env
    c=client(); sup=client()
    login(c,'counter1@example.com','counter-password123!')
    login(sup,'super@example.com','firstloginpassword123!')
    sid=start(c,ids['store'])
    post(c,f'/inventory/{sid}/scan',{'upc':'000123456789'})
    post(sup,f'/super-admin/delete-user/{ids["u1"]}')
    with a.app.app_context():
        assert a.db.session.get(a.InventorySession,sid).created_by.full_name=='Counter One'
        assert a.db.session.get(a.User,ids['u1']).status=='archived'
        assert not a.UserSubLocationAccess.query.filter_by(user_id=ids['u1']).first()
    assert c.get('/dashboard').status_code==302
    post(sup,f'/super-admin/delete-gl/{ids["gl"]}')
    with a.app.app_context():
        assert not a.db.session.get(a.GLAccount,ids['gl']).active
        a.ensure_seed_data() # restart/init must not reactivate deleted GL
        assert not a.db.session.get(a.GLAccount,ids['gl']).active


def test_master_upload_rejects_bad_row_no_partial_import_and_allows_each_or_case(env):
    ids,_=env
    c=client(); login(c,'super@example.com','firstloginpassword123!')
    csv_data='Item Name,Each UPC Code,Each Cost,Case UPC Code,Case Cost,GL Account\nHoney,100007,2.50,,,60110020\nBad item,100008,not-a-number,,,60110020\n'
    r=post(c,'/super-admin/upload-master',{'company_id':str(ids['company']),
        'file':(io.BytesIO(csv_data.encode()),'master.csv')},content_type='multipart/form-data')
    assert 'Nothing imported' in r.get_data(as_text=True)
    with a.app.app_context():
        assert not a.ItemMaster.query.filter_by(each_upc='100007').first()
    csv_good='Item Name,Each UPC Code,Each Cost,Case UPC Code,Case Cost,GL Account\nHoney,100007,2.50,,,60110020\nBulk Rice,,,99001,32.20,60110020\n'
    r=post(c,'/super-admin/upload-master',{'company_id':str(ids['company']),
        'file':(io.BytesIO(csv_good.encode()),'master.csv')},content_type='multipart/form-data')
    assert '2 added' in r.get_data(as_text=True)
    with a.app.app_context():
        assert a.ItemMaster.query.filter_by(case_upc='99001').first()


def test_admin_can_request_account_only_for_assigned_location_and_super_approve(env):
    ids,_=env
    m=client(); sup=client()
    login(m,'manager@example.com','manager-password123!')
    login(sup,'super@example.com','firstloginpassword123!')
    assert post(m,'/admin',{'action':'request_user','full_name':'New Person','email':'newperson@example.com',
        'sub_location_id':str(ids['hall'])}).status_code==403
    post(m,'/admin',{'action':'request_user','full_name':'New Person','email':'newperson@example.com',
        'sub_location_id':str(ids['store'])})
    with a.app.app_context():
        rid=a.UserAccessRequest.query.filter_by(email='newperson@example.com').one().id
    post(sup,f'/super-admin/account-request/{rid}/approve')
    with a.app.app_context():
        u=a.User.query.filter_by(email='newperson@example.com').one()
        assert u.initial_password_reset_required
        assert a.UserSubLocationAccess.query.filter_by(user_id=u.id,sub_location_id=ids['store']).one()


def test_csrf_blocks_external_posts(env):
    c=client(); c.get('/login')
    r=c.post('/login',data={'email':'counter1@example.com','password':'counter-password123!'})
    assert r.status_code==400


def test_all_major_pages_render_without_500_and_permissions_hold(env):
    ids,_=env
    su=client(); m=client(); u=client()
    login(su,'super@example.com','firstloginpassword123!')
    login(m,'manager@example.com','manager-password123!')
    login(u,'counter1@example.com','counter-password123!')
    for path in ('/dashboard','/profile','/reports','/inventory/start','/admin','/super-admin',
                 '/super-admin/upload-master',f'/super-admin/download-master/{ids["company"]}'):
        response=su.get(path)
        assert response.status_code==200, (path, response.status_code)
    assert m.get('/admin').status_code==200
    assert m.get('/super-admin').status_code==302
    assert u.get('/super-admin').status_code==302
    assert u.get('/admin').status_code==302
    assert u.get('/healthz').status_code==200


def test_email_templates_approval_links_and_admin_only_gl_email(env):
    ids, outgoing = env
    manager = client(); counter = client(); superuser = client()
    login(manager, 'manager@example.com', 'manager-password123!')
    login(counter, 'counter1@example.com', 'counter-password123!')
    login(superuser, 'super@example.com', 'firstloginpassword123!')
    # Super admin can edit the invitation wording, and the login URL is substituted.
    res = post(superuser, '/super-admin', {'action':'save_email_template',
        'email_event':'account_created', 'email_subject':'Welcome {name}',
        'email_body':'Use {login_url} to sign in, {name}. Password: {temporary_password}'})
    assert res.status_code == 200
    with a.app.app_context():
        subject, body = a.render_email('account_created', {'name':'Test Person',
            'login_url':'https://dashboard.invotra.us/login', 'temporary_password':'abc123'})
        assert subject == 'Welcome Test Person'
        assert 'https://dashboard.invotra.us/login' in body
    sid = start(counter, ids['store'])
    post(counter, f'/inventory/{sid}/scan', {'upc':'000123456789'})
    post(counter, f'/inventory/{sid}/submit')
    submitted = [x for x in outgoing.call_args_list if x.kwargs.get('event')=='inventory_submitted_admin']
    assert len(submitted)==1
    assert f'https://dashboard.invotra.us/admin/session/{sid}' in submitted[0].args[2]
    post(manager, f'/admin/session/{sid}', {'action':'approve'})
    approved = [x for x in outgoing.call_args_list if x.kwargs.get('event')=='inventory_approved']
    assert len(approved)==1
    assert approved[0].kwargs.get('attachments') is None and len(approved[0].args)==3
    assert approved[0].args[0] == ['manager@example.com']
    assert '60110020' in approved[0].args[2]
    assert f'https://dashboard.invotra.us/admin/session/{sid}' in approved[0].args[2]
    # A sample approval email can be tested without approving another session.
    assert post(superuser, '/super-admin/test-gl-summary').status_code==200
    assert any(x.kwargs.get('event')=='inventory_approved_test' for x in outgoing.call_args_list)


def test_email_html_escapes_product_names():
    safe=a.mail_html('Product <script>alert(1)</script>\nhttps://dashboard.invotra.us/login')
    assert '<script>' not in safe
    assert '&lt;script&gt;' in safe
    assert '<a href="https://dashboard.invotra.us/login"' in safe
