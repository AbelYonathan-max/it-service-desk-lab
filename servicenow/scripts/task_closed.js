/*
 * Business rule: Kestrel - close item with its task
 * Catalogue task, after update, when the task closes (complete, incomplete or skipped).
 */
(function executeRule(current, previous) {
    if (!current.state.changes()) {
        return;
    }
    new KestrelServiceDesk().taskClosed(current);
})(current, previous);
