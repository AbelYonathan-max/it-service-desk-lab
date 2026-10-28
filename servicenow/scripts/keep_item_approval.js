/*
 * Business rule: Kestrel - keep item approval in step
 * Requested item, before update. The request's own approval cascades "approved" down to its items;
 * while our approvals are still outstanding, the item stays "requested" so that it is true and the
 * fulfilment SLA stays paused.
 */
(function executeRule(current, previous) {
    if (!current.approval.changesTo('approved')) {
        return;
    }
    var desk = new KestrelServiceDesk();
    if (desk.isManaged(current) && desk.pendingApprovals(current) > 0) {
        current.approval = 'requested';
    }
})(current, previous);
