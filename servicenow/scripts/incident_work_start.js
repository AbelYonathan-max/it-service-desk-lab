/*
 * Business rule: Kestrel - record when work starts
 * Incident, before insert or update. Sets "Actual start" (work_start) the first time an incident
 * goes to In Progress, or is resolved straight away. The response SLA stops at that moment and
 * the monthly report measures response time from it.
 */
(function executeRule(current, previous /* null on insert */) {
    if (!current.work_start.nil()) {
        return;
    }
    var state = String(current.state);
    if ((state === '2' || state === '6' || state === '7') && (!previous || current.state.changes())) {
        current.work_start = new GlideDateTime();
    }
})(current, previous);
