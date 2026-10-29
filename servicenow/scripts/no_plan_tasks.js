/*
 * Business rule: Kestrel - no execution plan tasks
 * Catalogue task, before insert, for a task generated from an execution plan. A catalogue item with
 * no workflow or flow gets the instance's default execution plan, whose tasks ("Assess or Scope
 * Task" for Field Services, then "Provide requested service" for Software) appear as soon as the
 * item is ordered, before anyone has approved it, and keep it open after its own task is done. The
 * automated items have their own single task, so their plan tasks are never created.
 */
(function executeRule(current, previous /* null on insert */) {
    var ritm = new GlideRecord('sc_req_item');
    if (ritm.get(current.getValue('request_item')) && new KestrelServiceDesk().itemConfig(ritm) !== null) {
        current.setAbortAction(true);
    }
})(current, previous);
