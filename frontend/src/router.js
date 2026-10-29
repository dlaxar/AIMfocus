import { createRouter, createWebHashHistory } from 'vue-router'
import IAmView from './views/IAmView.vue'
import PatientView from './views/PatientView.vue'
import PatientDashboardView from './views/PatientDashboardView.vue'
import ScreenView from './views/ScreenView.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: IAmView },
    { path: '/patient', component: PatientView },
    { path: '/patient/dashboard', component: PatientDashboardView },
    // Core result screen (session variables, sources, live updates); reachable directly
    { path: '/screen', component: ScreenView },
  ],
})
