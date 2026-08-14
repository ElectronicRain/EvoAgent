import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: () => import('./views/DashboardView.vue'), meta: { title: '运行总览' } },
    { path: '/research', component: () => import('./views/ResearchView.vue'), meta: { title: '科研空间' } },
    { path: '/research/:projectId/:section?', component: () => import('./views/ResearchProjectView.vue'), meta: { title: '科研项目' } },
    { path: '/learning', component: () => import('./views/LearningView.vue'), meta: { title: '学习空间' } },
    { path: '/learning/:projectId/:section?', component: () => import('./views/LearningProjectView.vue'), meta: { title: '学习项目' } },
    { path: '/agents', component: () => import('./views/AgentsView.vue'), meta: { title: 'Agent 工厂' } },
    { path: '/workflows', component: () => import('./views/WorkflowsView.vue'), meta: { title: '协作工作流' } },
    { path: '/knowledge', component: () => import('./views/KnowledgeView.vue'), meta: { title: '学科知识库' } },
    { path: '/knowledge/:id', component: () => import('./views/KnowledgeDetailView.vue'), meta: { title: '知识库详情', detached: true } },
    { path: '/extensions', component: () => import('./views/ExtensionsView.vue'), meta: { title: '扩展与模型' } },
    { path: '/evolution', component: () => import('./views/EvolutionView.vue'), meta: { title: '进化实验室' } },
    { path: '/perception', component: () => import('./views/UserPerceptionView.vue'), meta: { title: '用户与感知' } },
    { path: '/governance', component: () => import('./views/GovernanceView.vue'), meta: { title: '安全治理' } },
  ],
})

export default router
