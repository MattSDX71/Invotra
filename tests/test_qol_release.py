"""Run alongside existing tests: python -m pytest -q

Uses existing disposable SQLite fixture and mocked email transport.
"""
from decimal import Decimal
import test_workflows as t
import app as a


def test_fractional_each_case_and_independent_sessions(env):
    ids, _ = env
    c1 = t.client(); c2 = t.client()
    t.login(c1,'counter1@example.com','counter-password123!')
    t.login(c2,'counter2@example.com','counter-password123!')
    sid1 = t.start(c1,ids['store']); sid2 = t.start(c2,ids['store'])
    page = c1.get(f'/inventory/{sid1}').get_data(as_text=True)
    assert 'scan-quantities' in page and 'value="0.25"' in page
    for q in ('0.25','0.5','0.75','1'):
        result = t.post(c1,f'/inventory/{sid1}/scan',{'upc':'000123456789','scan_qty':q})
        assert result.status_code == 200
    t.post(c1,f'/inventory/{sid1}/scan',{'upc':'999999999999','scan_qty':'0.25'})
    t.post(c2,f'/inventory/{sid2}/scan',{'upc':'999999999999','scan_qty':'0.75'})
    with a.app.app_context():
        a.db.session.expire_all()
        one = a.db.session.get(a.InventorySession,sid1)
        two = a.db.session.get(a.InventorySession,sid2)
        assert one.total_quantity == Decimal('2.75')
        assert one.total_value == Decimal('6.75')
        assert two.total_quantity == Decimal('0.75')
        assert two.total_value == Decimal('9.00')
    assert t.post(c1,f'/inventory/{sid1}/scan',{'upc':'000123456789','scan_qty':'0.333'}).status_code==200
    with a.app.app_context():
        assert a.db.session.get(a.InventorySession,sid1).total_quantity == Decimal('2.75')


def test_fractional_missing_item_persists_through_admin_pricing(env):
    ids, _ = env
    c=t.client(); manager=t.client()
    t.login(c,'counter1@example.com','counter-password123!')
    t.login(manager,'manager@example.com','manager-password123!')
    sid=t.start(c,ids['store'])
    r=t.post(c,f'/inventory/{sid}/scan',{'upc':'FRACTIONAL-UPC','scan_qty':'0.75'})
    assert 'New / Unrecognized Item' in r.get_data(as_text=True)
    assert 'name="scan_qty" value="0.75"' in r.get_data(as_text=True)
    t.post(c,f'/inventory/{sid}/unknown',{'scanned_upc':'FRACTIONAL-UPC','scan_qty':'0.75',
        'proposed_name':'Open juice case','proposed_gl_account_id':str(ids['gl'])})
    with a.app.app_context():
        item=a.UnknownItem.query.filter_by(session_id=sid).one()
        assert item.session_ref.lines[0].quantity == Decimal('0.75')
        unknown_id=item.id
    t.post(c,f'/inventory/{sid}/submit')
    t.post(manager,f'/admin/unknown/{unknown_id}',{'action':'approve','item_name':'Open juice case',
        'each_upc':'','each_cost':'','case_upc':'FRACTIONAL-UPC','case_cost':'20.00',
        'gl_account_id':str(ids['gl'])})
    with a.app.app_context():
        inv=a.db.session.get(a.InventorySession,sid)
        assert inv.total_quantity == Decimal('0.75')
        assert inv.total_value == Decimal('15.00')


def test_parent_filter_and_location_scoped_approval_report_email(env):
    ids, mail=env
    with a.app.app_context():
        foreign=a.ParentLocation(company_id=ids['company'],name='Other University')
        a.db.session.add(foreign);a.db.session.flush()
        foreign_sub=a.SubLocation(parent_id=foreign.id,name='Other Store')
        a.db.session.add(foreign_sub);a.db.session.flush()
        other=a.User(full_name='Other Person',email='other@example.com',role='user',status='active')
        other.set_password('another-password-1234!')
        a.db.session.add(other);a.db.session.flush()
        a.db.session.add(a.UserSubLocationAccess(user_id=other.id,sub_location_id=foreign_sub.id))
        rule=a.ParentNotificationRule(parent_id=ids['parent'],event='inventory_approved',
                                      extra_recipients='district-review@example.com')
        a.db.session.add(rule);a.db.session.commit()
        assert 'other@example.com' not in a.location_report_recipients(ids['store'])
    su=t.client(); m=t.client(); counter=t.client(); second=t.client()
    t.login(su,'super@example.com','firstloginpassword123!')
    t.login(m,'manager@example.com','manager-password123!')
    t.login(counter,'counter1@example.com','counter-password123!')
    t.login(second,'counter2@example.com','counter-password123!')
    page=su.get('/super-admin').get_data(as_text=True)
    assert 'Select a parent to view its users' in page
    page=su.get(f'/super-admin?parent_id={ids["parent"]}').get_data(as_text=True)
    assert 'Counter One' in page and 'Other Person' not in page
    selected=m.get(f'/admin?parent_id={ids["parent"]}')
    assert selected.status_code==200
    sid=t.start(counter,ids['store'])
    t.post(counter,f'/inventory/{sid}/scan',{'upc':'000123456789'})
    assert second.get(f'/inventory/{sid}').status_code==302
    t.post(counter,f'/inventory/{sid}/submit')
    submit_admin=[x for x in mail.call_args_list if x.kwargs.get('event')=='inventory_submitted_admin'][-1]
    assert 'manager@example.com' in submit_admin.args[0]
    t.post(m,f'/admin/session/{sid}',{'action':'approve'})
    approved=[x for x in mail.call_args_list if x.kwargs.get('event')=='inventory_approved'][-1]
    recipients=approved.args[0]
    assert set(['counter1@example.com','counter2@example.com','manager@example.com',
                'district-review@example.com']).issubset(set(recipients))
    assert 'other@example.com' not in recipients
    assert second.get(f'/inventory/{sid}').status_code == 200
    assert second.get(f'/inventory/{sid}/csv').status_code == 200
