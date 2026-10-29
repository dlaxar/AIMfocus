import { createRouter, createWebHashHistory } from 'vue-router'
import IAmView from './views/IAmView.vue'
import PatientView from './views/PatientView.vue'
import PatientDashboardView from './views/PatientDashboardView.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: IAmView },
    { path: '/patient', component: PatientView },
    { path: '/patient/dashboard', component: PatientDashboardView },
  ],
})
