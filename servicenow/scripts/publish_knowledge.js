/*
 * Scheduled job (run once): Kestrel - publish knowledge articles
 * The Table API cannot move a knowledge article out of Draft, so `sdlab sn apply` starts this job
 * with the sys_ids of the articles it loaded in place of ARTICLE_IDS, then deletes the job.
 */
(function publishKnowledge() {
    var article = new GlideRecord('kb_knowledge');
    article.addQuery('sys_id', 'IN', 'ARTICLE_IDS');
    article.addQuery('workflow_state', 'IN', 'draft,review');
    article.query();
    while (article.next()) {
        article.setValue('workflow_state', 'published');
        article.setValue('published', new GlideDate().getValue());
        // Without this the rules that wait for a person to press Publish put the article back in Draft.
        article.setWorkflow(false);
        article.update();
    }
})();
