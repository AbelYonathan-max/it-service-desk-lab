/*
 * Business rule: Kestrel - request approvals
 * Requested item, async after insert. Async so the catalogue variables are saved before it runs.
 */
(function executeRule(current, previous /* null when async */) {
    new KestrelServiceDesk().requestApprovals(current);
})(current, previous);
