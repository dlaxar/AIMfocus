import { createRouter, createWebHashHistory } from 'vue-router'
import IAmView from './views/IAmView.vue'
import PatientView from './views/PatientView.vue'
import PatientDashboardView from './views/PatientDashboardView.vue'
import StatsView from './views/StatsView.vue'
export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: IAmView },
    { path: '/patient', component: PatientView },
    { path: '/patient/dashboard', component: PatientDashboardView },
    { path: '/stats', component: StatsView },
  ],
})
