/*
 * Scheduled job (run once): Kestrel - put history in place
 * `sdlab sn history` back-dates records through the Table API, which cannot set everything: a change
 * model moves a change one state at a time, each step with its own checks and approvals. For those
 * values it starts this job, with RECORDS replaced by a JSON object (table, then sys_id, then the
 * fields to set), then deletes the job.
 */
(function putHistory() {
    var records = RECORDS;
    for (var table in records) {
        if (!records.hasOwnProperty(table)) {
            continue;
        }
        var rows = records[table];
        for (var id in rows) {
            if (!rows.hasOwnProperty(id)) {
                continue;
            }
            var record = new GlideRecord(table);
            if (!record.get(id)) {
                continue;
            }
            var fields = rows[id];
            for (var name in fields) {
                if (fields.hasOwnProperty(name)) {
                    record.setValue(name, fields[name]);
                }
            }
            // History, not new work: business rules, the change model and approval flows stay out of it.
            record.setWorkflow(false);
            record.update();
        }
    }
})();
