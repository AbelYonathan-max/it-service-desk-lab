/*
 * Business rule: Kestrel - approval decided
 * Approval, after insert or update, when an approval for a requested item is approved or rejected.
 */
(function executeRule(current, previous /* null on insert */) {
    if (previous && String(previous.state) === String(current.state)) {
        return;
    }
    var ritm = new GlideRecord('sc_req_item');
    if (ritm.get(current.getValue('sysapproval'))) {
        new KestrelServiceDesk().decide(ritm);
    }
})(current, previous);
