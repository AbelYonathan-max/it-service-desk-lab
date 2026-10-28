/*
 * Script include: KestrelServiceDesk
 *
 * Approvals and fulfilment for the automated "People and access" catalogue items (new starter,
 * leaver, change of role, shared drive access). The rules are:
 *   - approve where access is granted: the line manager for a starter, the new line manager for a
 *     move, the data owner for a shared drive;
 *   - never delay taking access away: a leaver needs no approval;
 *   - once approved, one catalogue task goes to "Identity and Access Automation", which the
 *     fulfilment agent on the domain controller picks up and carries out in Active Directory;
 *   - when that task closes, the requested item and the request close with it.
 *
 * Settings come from the system property kestrel.servicedesk.catalog (JSON), which
 * `sdlab sn apply` writes from servicenow/config/catalog.yaml. Simulated history (correlation ID
 * starting SIM-) is ignored. Written for the global scope's ES5 engine.
 */
var KestrelServiceDesk = Class.create();
KestrelServiceDesk.prototype = {
    initialize: function () {
        this.config = this._loadConfig();
    },

    _loadConfig: function () {
        var raw = gs.getProperty('kestrel.servicedesk.catalog', '{}');
        try {
            var parsed = JSON.parse(raw);
            parsed.items = parsed.items || {};
            parsed.share_owners = parsed.share_owners || {};
            return parsed;
        } catch (e) {
            gs.error('KestrelServiceDesk: kestrel.servicedesk.catalog is not valid JSON: ' + e);
            return { items: {}, share_owners: {} };
        }
    },

    /* The item's settings, or null when this is not one of our automated items. */
    itemConfig: function (ritm) {
        var name = String(ritm.cat_item.name);
        return this.config.items.hasOwnProperty(name) ? this.config.items[name] : null;
    },

    isSimulated: function (record) {
        return String(record.correlation_id).indexOf('SIM-') === 0;
    },

    isManaged: function (ritm) {
        return this.itemConfig(ritm) !== null && !this.isSimulated(ritm);
    },

    variable: function (ritm, name) {
        var element = ritm.variables[name];
        return element ? String(element) : '';
    },

    variableDisplay: function (ritm, name) {
        var element = ritm.variables[name];
        return element ? String(element.getDisplayValue()) : '';
    },

    /* Called once, after a requested item is created (async rule, so its variables are saved). */
    requestApprovals: function (ritm) {
        if (!this.isManaged(ritm) || this._countApprovals(ritm, '') > 0 || this._fulfilmentTask(ritm)) {
            return;
        }
        var item = this.itemConfig(ritm);
        if (item.approval === 'none') {
            // Recorded as approved, which is what the fulfilment agent looks for.
            ritm.approval = 'approved';
            ritm.work_notes = 'No approval needed: ' + (item.approval_note || 'this request only removes access') + '.';
            ritm.update();
            this.startFulfilment(ritm, item);
            return;
        }

        var approver = this._approver(ritm, item);
        if (!approver) {
            ritm.work_notes = 'Could not work out who should approve this (' + item.approval +
                '). Passed to ' + this.config.fallback_group + ' to find the approver.';
            ritm.update();
            this._createTask(ritm, this.config.fallback_group, 'Find the approver, then fulfil: ' + this._summary(ritm, item));
            return;
        }

        var raisedByApprover = String(ritm.opened_by) === approver.getUniqueValue();
        var approval = new GlideRecord('sysapproval_approver');
        approval.initialize();
        approval.setValue('sysapproval', ritm.getUniqueValue());
        approval.setValue('document_id', ritm.getUniqueValue());
        approval.setValue('source_table', 'sc_req_item');
        approval.setValue('approver', approver.getUniqueValue());
        approval.setValue('state', raisedByApprover ? 'approved' : 'requested');
        approval.comments = raisedByApprover ?
            'Approved automatically: the ' + this._approverRole(item) + ' raised this request.' :
            'Approval needed from the ' + this._approverRole(item) + ': ' + this._summary(ritm, item);
        approval.insert();

        if (!raisedByApprover) {
            ritm.approval = 'requested';
            ritm.stage = 'waiting_for_approval';
            ritm.work_notes = 'Waiting for approval from ' + approver.getDisplayValue() + ' (' + this._approverRole(item) + ').';
            ritm.update();
        }
        this.decide(ritm);
    },

    /* Called when an approval is decided. Rejection closes the item; full approval starts fulfilment. */
    decide: function (ritm) {
        if (!this.isManaged(ritm)) {
            return;
        }
        var item = this.itemConfig(ritm);
        if (this._countApprovals(ritm, 'rejected') > 0) {
            if (String(ritm.approval) !== 'rejected') {
                ritm.approval = 'rejected';
                ritm.state = 4; // Closed Incomplete
                ritm.stage = 'Request Cancelled';
                ritm.work_notes = 'Rejected by the ' + this._approverRole(item) + '. Nothing was changed.';
                ritm.update();
                this.closeRequestIfDone(ritm);
            }
            return;
        }
        if (this._countApprovals(ritm, 'requested') > 0 || this._countApprovals(ritm, 'approved') === 0) {
            return;
        }
        this.startFulfilment(ritm, item);
    },

    startFulfilment: function (ritm, item) {
        if (this._fulfilmentTask(ritm)) {
            return;
        }
        ritm.approval = 'approved';
        ritm.stage = 'fulfillment';
        ritm.work_notes = 'Sent to ' + this.config.automation_group + '. The fulfilment agent normally completes it within a few minutes.';
        ritm.update();
        this._createTask(ritm, this.config.automation_group, this._summary(ritm, item));
    },

    /* After a fulfilment task closes: close the item, then the request once all its items are closed. */
    taskClosed: function (task) {
        var ritm = new GlideRecord('sc_req_item');
        if (!ritm.get(task.getValue('request_item')) || !this.isManaged(ritm) || !ritm.active) {
            return;
        }
        var open = new GlideRecord('sc_task');
        open.addQuery('request_item', ritm.getUniqueValue());
        open.addActiveQuery();
        open.setLimit(1);
        open.query();
        if (open.hasNext()) {
            return;
        }
        var complete = String(task.state) === '3';
        ritm.state = complete ? 3 : 4;
        ritm.stage = complete ? 'complete' : 'Request Cancelled';
        ritm.work_notes = 'Closed with ' + task.number + ': ' + (task.close_notes.nil() ? task.state.getDisplayValue() : task.close_notes);
        ritm.update();
        this.closeRequestIfDone(ritm);
    },

    closeRequestIfDone: function (ritm) {
        var request = new GlideRecord('sc_request');
        if (!request.get(ritm.getValue('request')) || !request.active) {
            return;
        }
        var items = new GlideRecord('sc_req_item');
        items.addQuery('request', request.getUniqueValue());
        items.query();
        var total = 0, rejected = 0, incomplete = 0;
        while (items.next()) {
            if (items.active) {
                return;
            }
            total++;
            if (String(items.approval) === 'rejected') {
                rejected++;
            } else if (String(items.state) !== '3') {
                incomplete++;
            }
        }
        request.request_state = rejected === total ? 'closed_rejected' : (incomplete > 0 ? 'closed_incomplete' : 'closed_complete');
        request.state = rejected + incomplete > 0 ? 4 : 3;
        request.update();
    },

    /* While our own approvals are outstanding, the item's approval field must say so. */
    pendingApprovals: function (ritm) {
        return this._countApprovals(ritm, 'requested');
    },

    _approver: function (ritm, item) {
        var user = new GlideRecord('sys_user');
        var id = '';
        if (item.approval === 'line_manager') {
            id = this.variable(ritm, 'line_manager') || String(ritm.requested_for.manager);
        } else if (item.approval === 'new_line_manager') {
            id = this.variable(ritm, 'new_line_manager');
            if (!id) {
                var employee = new GlideRecord('sys_user');
                if (employee.get(this.variable(ritm, 'employee'))) {
                    id = employee.getValue('manager');
                }
            }
        } else if (item.approval === 'data_owner') {
            var owner = this.config.share_owners[this.variable(ritm, 'share')];
            if (owner && user.get('employee_number', owner) && user.active) {
                return user;
            }
            return null;
        }
        if (id && user.get(id) && user.active) {
            return user;
        }
        return null;
    },

    _approverRole: function (item) {
        return { line_manager: 'line manager', new_line_manager: 'new line manager', data_owner: 'data owner' }[item.approval] || 'approver';
    },

    _summary: function (ritm, item) {
        var self = this;
        var text = String(item.task || ritm.cat_item.name);
        return text.replace(/\{(\w+)\}/g, function (match, name) {
            return self.variableDisplay(ritm, name) || '?';
        });
    },

    _countApprovals: function (ritm, state) {
        var count = new GlideAggregate('sysapproval_approver');
        count.addQuery('sysapproval', ritm.getUniqueValue());
        if (state) {
            count.addQuery('state', state);
        }
        count.addAggregate('COUNT');
        count.query();
        return count.next() ? parseInt(count.getAggregate('COUNT'), 10) : 0;
    },

    _fulfilmentTask: function (ritm) {
        var task = new GlideRecord('sc_task');
        task.addQuery('request_item', ritm.getUniqueValue());
        task.addQuery('assignment_group.name', 'IN', this.config.automation_group + ',' + this.config.fallback_group);
        task.setLimit(1);
        task.query();
        return task.next();
    },

    _createTask: function (ritm, groupName, summary) {
        var group = new GlideRecord('sys_user_group');
        if (!group.get('name', groupName)) {
            gs.error('KestrelServiceDesk: no group called ' + groupName);
            return;
        }
        var task = new GlideRecord('sc_task');
        task.initialize();
        task.setValue('request_item', ritm.getUniqueValue());
        task.setValue('request', ritm.getValue('request'));
        task.setValue('assignment_group', group.getUniqueValue());
        task.setValue('short_description', summary.substring(0, 160));
        task.setValue('description', 'Raised by ' + ritm.number + ' (' + ritm.cat_item.name + ') for ' + ritm.requested_for.getDisplayValue() + '.');
        task.setValue('state', 1);
        task.insert();
    },

    type: 'KestrelServiceDesk'
};
