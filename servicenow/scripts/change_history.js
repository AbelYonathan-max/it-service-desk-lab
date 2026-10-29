/*
 * Scheduled job (run once): Kestrel - load change history
 * A change model moves a change one state at a time, each step with its own checks and approvals,
 * so `sdlab sn history` cannot back-date a change's states through the Table API. It starts this
 * job instead, with each change's final state, dates and outcome in place of CHANGES (a JSON
 * object keyed by sys_id), then deletes the job.
 */
(function loadChangeHistory() {
    var changes = CHANGES;
    for (var id in changes) {
        if (!changes.hasOwnProperty(id)) {
            continue;
        }
        var change = new GlideRecord('change_request');
        if (!change.get(id)) {
            continue;
        }
        var fields = changes[id];
        for (var name in fields) {
            if (fields.hasOwnProperty(name)) {
                change.setValue(name, fields[name]);
            }
        }
        // History, not new work: the change model's checks and the approval flow stay out of it.
        change.setWorkflow(false);
        change.update();
    }
})();
