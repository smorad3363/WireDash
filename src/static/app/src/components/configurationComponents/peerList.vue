<script setup async>
import {computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch} from "vue";
import {useRoute} from "vue-router";
import {fetchGet} from "@/utilities/fetch.js";
import ProtocolBadge from "@/components/protocolBadge.vue";
import LocaleText from "@/components/text/localeText.vue";
import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";
import {WireguardConfigurationsStore} from "@/stores/WireguardConfigurationsStore.js";
import PeerDataUsageCharts from "@/components/configurationComponents/peerListComponents/peerDataUsageCharts.vue";
import PeerSearch from "@/components/configurationComponents/peerSearch.vue";
import Peer from "@/components/configurationComponents/peer.vue";
import PeerListModals from "@/components/configurationComponents/peerListComponents/peerListModals.vue";
import ConfigurationDescription from "@/components/configurationComponents/configurationDescription.vue";
import PeerDetailsModal from "@/components/configurationComponents/peerDetailsModal.vue";

// Async Components
const PeerSearchBar = defineAsyncComponent(() => import("@/components/configurationComponents/peerSearchBar.vue"))
const PeerJobsAllModal = defineAsyncComponent(() => import("@/components/configurationComponents/peerJobsAllModal.vue"))
const PeerJobsLogsModal = defineAsyncComponent(() => import("@/components/configurationComponents/peerJobsLogsModal.vue"))
const EditConfigurationModal = defineAsyncComponent(() => import("@/components/configurationComponents/editConfiguration.vue"))
const SelectPeersModal = defineAsyncComponent(() => import("@/components/configurationComponents/selectPeers.vue"))
const PeerAddModal = defineAsyncComponent(() => import("@/components/configurationComponents/peerAddModal.vue"))

const dashboardStore = DashboardConfigurationStore()
const wireguardConfigurationStore = WireguardConfigurationsStore()
const route = useRoute()
const configurationInfo = ref({})
const configurationPeers = ref([])
const chartPeers = ref([])
const bulkPeers = ref([])
const peerPage = ref(1)
const peerPageSize = 50
const totalPeers = ref(0)
const filteredPeers = ref(0)
const configurationSummary = ref({connectedPeers: 0, totalUsage: 0, totalReceive: 0, totalSent: 0})
const pageCount = computed(() => Math.max(1, Math.ceil(filteredPeers.value / peerPageSize)))
const configurationToggling = ref(false)
const configurationModalSelectedPeer = ref({})
const configurationModals = ref({
	peerNew: {
		modalOpen: false	
	},
	peerSetting: {
		modalOpen: false,
	},
	peerScheduleJobs:{
		modalOpen: false,
	},
	peerQRCode: {
		modalOpen: false,
	},
	peerConfigurationFile: {
		modalOpen: false,
	},
	peerCreate: {
		modalOpen: false
	},
	peerScheduleJobsAll: {
		modalOpen: false
	},
	peerScheduleJobsLogs: {
		modalOpen: false
	},
	peerShare:{
		modalOpen: false,
	},
	editConfiguration: {
		modalOpen: false
	},
	selectPeers: {
		modalOpen: false
	},
	backupRestore: {
		modalOpen: false
	},
	deleteConfiguration: {
		modalOpen: false
	},
	editRawConfigurationFile: {
		modalOpen: false
	},
	assignPeer: {
		modalOpen: false
	},
	peerDetails: {
		modalOpen: false
	}
})
const peerSearchBar = ref(false)
// Fetch ONLY the requested UI page. Existing bot APIs remain unchanged.
let peerListLoading = false
let peerListRefreshQueued = false
const fetchPeerList = async () => {
	if (peerListLoading) {
		peerListRefreshQueued = true
		return
	}
	peerListLoading = true
	try {
		await fetchGet("/api/ui/getWireguardConfigurationPage", {
			configurationName: route.params.id,
			page: peerPage.value,
			perPage: peerPageSize,
			search: wireguardConfigurationStore.searchString || "",
			sort: dashboardStore.Configuration.Server.dashboard_sort || "name",
			hiddenTags: wireguardConfigurationStore.Filter.HiddenTags.join(","),
			showAllWhenHidden: wireguardConfigurationStore.Filter.ShowAllPeersWhenHiddenTags
		}, (res) => {
			if (res.status) {
				configurationInfo.value = res.data.configurationInfo
				configurationPeers.value = res.data.configurationPeers
				chartPeers.value = res.data.chartPeers
				configurationSummary.value = res.data.summary
				totalPeers.value = res.data.totalPeers
				filteredPeers.value = res.data.filteredPeers
				peerPage.value = res.data.page
			}
		})
	} finally {
		peerListLoading = false
		if (peerListRefreshQueued) {
			peerListRefreshQueued = false
			await fetchPeerList()
		}
	}
}
await fetchPeerList()
		}
	}
}
await fetchPeerList()

// Fetch Peer Interval =====================================
const fetchPeerListInterval = ref(undefined)
const setFetchPeerListInterval = () => {
	clearInterval(fetchPeerListInterval.value)
	const refreshMs = Math.max(10000, Number(dashboardStore.Configuration.Server.dashboard_refresh_interval) || 60000)
	fetchPeerListInterval.value = setInterval(() => {
		// Avoid wasting JSON serialization and chart work in background tabs.
		if (!document.hidden) fetchPeerList()
	}, refreshMs)
}
setFetchPeerListInterval()
onBeforeUnmount(() => {
	clearInterval(fetchPeerListInterval.value);
	fetchPeerListInterval.value = undefined;
	wireguardConfigurationStore.Filter.HiddenTags = []
})

watch(() => {
	return dashboardStore.Configuration.Server.dashboard_refresh_interval
}, () => {
	setFetchPeerListInterval()
})

// Toggle Configuration Method =====================================
const toggleConfiguration = async () => {
	configurationToggling.value = true;
	await fetchGet("/api/toggleWireguardConfiguration", {
		configurationName: configurationInfo.value.Name
	}, (res) => {
		if (res.status){
			dashboardStore.newMessage("Server", 
				`${configurationInfo.value.Name} ${res.data ? 'is on':'is off'}`, "success")
		}else{
			dashboardStore.newMessage("Server", res.message, 'danger')
		}
		wireguardConfigurationStore.Configurations
			.find(x => x.Name === configurationInfo.value.Name).Status = res.data
		configurationInfo.value.Status = res.data
		configurationToggling.value = false;
	})
}

// Server-side search, sort and paging; full lists are loaded only for bulk UI.
const searchPeers = computed(() => configurationPeers.value)
const gotoPeerPage = async (page) => {
	if (page < 1 || page > pageCount.value || page === peerPage.value) return
	peerPage.value = page
	await fetchPeerList()
}
const openBulkModal = async (modal) => {
	let loaded = false
	await fetchGet("/api/getWireguardConfigurationInfo", {
		configurationName: route.params.id
	}, (res) => {
		if (res.status) {
			bulkPeers.value = [
				...res.data.configurationPeers,
				...res.data.configurationRestrictedPeers.map(p => ({...p, restricted: true}))
			]
			loaded = true
		}
	})
	if (loaded) configurationModals.value[modal].modalOpen = true
}
watch(() => JSON.stringify([
	wireguardConfigurationStore.searchString || "",
	dashboardStore.Configuration.Server.dashboard_sort,
	wireguardConfigurationStore.Filter.HiddenTags,
	wireguardConfigurationStore.Filter.ShowAllPeersWhenHiddenTags,
]), () => {
	peerPage.value = 1
	fetchPeerList()
})

watch(() => route.query.id, (newValue) => {
	if (newValue){
		wireguardConfigurationStore.searchString = newValue
	}else{
		wireguardConfigurationStore.searchString = undefined
	}
}, {
	immediate: true
})
</script>

<template>
<div class="container-fluid" >
	<div class="d-flex align-items-sm-start flex-column flex-sm-row gap-3">
		<div>
			<div class="text-muted d-flex align-items-center gap-2">
				<h5 class="mb-0">
					<ProtocolBadge :protocol="configurationInfo.Protocol"></ProtocolBadge>
				</h5>
			</div>
			<div class="d-flex align-items-center gap-3">
				<h1 class="mb-0 display-4"><samp>{{configurationInfo.Name}}</samp></h1>
			</div>
		</div>
		<div class="ms-sm-auto d-flex gap-2 flex-column">
			<div class="card rounded-3 bg-transparent ">
				<div class="card-body py-2 d-flex align-items-center">
					<small class="text-muted">
						<LocaleText t="Status"></LocaleText>
					</small>
					<div class="dot ms-2" :class="{active: configurationInfo.Status}"></div>
					<div class="form-check form-switch mb-0 ms-auto pe-0 me-0">
						<label class="form-check-label" style="cursor: pointer" :for="'switch' + configurationInfo.id">
							<LocaleText t="On" v-if="configurationInfo.Status && !configurationToggling"></LocaleText>
							<LocaleText t="Off" v-else-if="!configurationInfo.Status && !configurationToggling"></LocaleText>
							<span v-if="configurationToggling"
							      class="spinner-border spinner-border-sm ms-2" aria-hidden="true">
							</span>
						</label>
						<input class="form-check-input"
						       style="cursor: pointer"
						       :disabled="configurationToggling"
						       type="checkbox" role="switch" :id="'switch' + configurationInfo.id"
						       @change="toggleConfiguration()"
						       v-model="configurationInfo.Status">
					</div>
				</div>
			</div>
			<div class="d-flex gap-2">
				<a
					role="button"
					@click="configurationModals.peerNew.modalOpen = true"
					class="titleBtn py-2 text-decoration-none btn text-primary-emphasis bg-primary-subtle rounded-3 border-1 border-primary-subtle ">
					<i class="bi bi-plus-circle me-2"></i>
					<LocaleText t="Peer"></LocaleText>
				</a>
				<button class="titleBtn py-2 text-decoration-none btn text-primary-emphasis bg-primary-subtle rounded-3 border-1 border-primary-subtle "
				        @click="configurationModals.editConfiguration.modalOpen = true"
				        type="button" aria-expanded="false">
					<i class="bi bi-gear-fill me-2"></i>
					<LocaleText t="Configuration Settings"></LocaleText>
				</button>
			</div>
		</div>
	</div>
	<hr>
	<ConfigurationDescription :configuration="configurationInfo"></ConfigurationDescription>
	<div class="row mt-3 gy-2 gx-2 mb-2">
		<div class="col-12 col-lg-3">
			<div class="card rounded-3 bg-transparent  h-100">
				<div class="card-body py-2 d-flex flex-column justify-content-center">
					<p class="mb-0 text-muted"><small>
						<LocaleText t="Address"></LocaleText>
					</small></p>
					{{configurationInfo.Address}}
				</div>
			</div>
		</div>
		<div class="col-12 col-lg-3">
			<div class="card rounded-3 bg-transparent h-100">
				<div class="card-body py-2 d-flex flex-column justify-content-center">
					<p class="mb-0 text-muted"><small>
						<LocaleText t="Listen Port"></LocaleText>
					</small></p>
					{{configurationInfo.ListenPort}}
				</div>
			</div>
		</div>
		<div style="word-break: break-all" class="col-12 col-lg-6">
			<div class="card rounded-3 bg-transparent h-100">
				<div class="card-body py-2 d-flex flex-column justify-content-center">
					<p class="mb-0 text-muted"><small>
						<LocaleText t="Public Key"></LocaleText>
					</small></p>
					<samp>{{configurationInfo.PublicKey}}</samp>
				</div>
			</div>
		</div>
	</div>
	<div class="row gx-2 gy-2 mb-2">
		<div class="col-12 col-lg-3">
			<div class="card rounded-3 bg-transparent  h-100">
				<div class="card-body d-flex">
					<div>
						<p class="mb-0 text-muted"><small>
							<LocaleText t="Connected Peers"></LocaleText>
						</small></p>
						<strong class="h4">
							{{configurationSummary.connectedPeers}} / {{totalPeers}}
						</strong>
					</div>
					<i class="bi bi-ethernet ms-auto h2 text-muted"></i>
				</div>
			</div>
		</div>
		<div class="col-12 col-lg-3">
			<div class="card rounded-3 bg-transparent  h-100">
				<div class="card-body d-flex">
					<div>
						<p class="mb-0 text-muted"><small>
							<LocaleText t="Total Usage"></LocaleText>
						</small></p>
						<strong class="h4">{{configurationSummary.totalUsage}} GB</strong>
					</div>
					<i class="bi bi-arrow-down-up ms-auto h2 text-muted"></i>
				</div>
			</div>
		</div>
		<div class="col-12 col-lg-3">
			<div class="card rounded-3 bg-transparent  h-100">
				<div class="card-body d-flex">
					<div>
						<p class="mb-0 text-muted"><small>
							Upload
						</small></p>
						<strong class="h4 text-primary">{{configurationSummary.totalReceive}} GB</strong>
					</div>
					<i class="bi bi-arrow-down ms-auto h2 text-muted"></i>
				</div>
			</div>
		</div>
		<div class="col-12 col-lg-3">
			<div class="card rounded-3 bg-transparent  h-100">
				<div class="card-body d-flex">
					<div>
						<p class="mb-0 text-muted"><small>
							Download
						</small></p>
						<strong class="h4 text-success">{{configurationSummary.totalSent}} GB</strong>
					</div>
					<i class="bi bi-arrow-up ms-auto h2 text-muted"></i>
				</div>
			</div>
		</div>
	</div>
	<PeerDataUsageCharts
		:configurationPeers="chartPeers"
		:configurationInfo="configurationInfo"
	></PeerDataUsageCharts>
	<small class="text-muted">Usage chart: top 30 peers by total metered traffic; upload and download remain separate.</small>
	<hr>
	<div style="margin-bottom: 10rem">
		<PeerSearch
			v-if="totalPeers > 0"
			@search="peerSearchBar = !peerSearchBar"
			@jobsAll="openBulkModal('peerScheduleJobsAll')"
			@jobLogs="configurationModals.peerScheduleJobsLogs.modalOpen = true"
			@editConfiguration="configurationModals.editConfiguration.modalOpen = true"
			@selectPeers="openBulkModal('selectPeers')"
			@backupRestore="configurationModals.backupRestore.modalOpen = true"
			@deleteConfiguration="configurationModals.deleteConfiguration.modalOpen = true"
			:configuration="configurationInfo">
		</PeerSearch>
		<TransitionGroup name="peerList" tag="div" class="row gx-2 gy-2 z-0 position-relative">
			<div class="col-12"
			     :class="{'col-lg-6 col-xl-4': dashboardStore.Configuration.Server.dashboard_peer_list_display === 'grid'}"
			     :key="peer.id"
			     v-for="(peer, order) in searchPeers">
				<Peer :Peer="peer"
					  :searchPeersLength="searchPeers.length"
					  :order="order"
					  :ConfigurationInfo="configurationInfo"
					  @details="configurationModals.peerDetails.modalOpen = true; configurationModalSelectedPeer = peer"
				      @share="configurationModals.peerShare.modalOpen = true; configurationModalSelectedPeer = peer"
				      @refresh="fetchPeerList()"

				      @jobs="configurationModals.peerScheduleJobs.modalOpen = true; configurationModalSelectedPeer = peer"
				      @setting="configurationModals.peerSetting.modalOpen = true; configurationModalSelectedPeer = peer"
				      @qrcode="configurationModalSelectedPeer = peer; configurationModals.peerQRCode.modalOpen = true;"
				      @configurationFile="configurationModalSelectedPeer = peer; configurationModals.peerConfigurationFile.modalOpen = true;"
				      @assign="configurationModalSelectedPeer = peer; configurationModals.assignPeer.modalOpen = true;"
				></Peer>
			</div>
		</TransitionGroup>
		
	</div>
	<Transition name="slide-fade">
		<PeerSearchBar
			v-if="peerSearchBar"
			:ConfigurationInfo="configurationInfo"
			@close="peerSearchBar = false"></PeerSearchBar>
	</Transition>
	<PeerListModals 
		:configurationModals="configurationModals"
		:configurationModalSelectedPeer="configurationModalSelectedPeer"
		@refresh="fetchPeerList()"
	></PeerListModals>
	<TransitionGroup name="zoom">
		<Suspense key="PeerAddModal">
			<PeerAddModal
				v-if="configurationModals.peerNew.modalOpen"
				@close="configurationModals.peerNew.modalOpen = false"
				@addedPeers="configurationModals.peerNew.modalOpen = false; fetchPeerList()"
			></PeerAddModal>
		</Suspense>
		<PeerJobsAllModal
			key="PeerJobsAllModal"
			v-if="configurationModals.peerScheduleJobsAll.modalOpen"
			@refresh="fetchPeerList()"
			@allLogs="configurationModals.peerScheduleJobsLogs.modalOpen = true"
			@close="configurationModals.peerScheduleJobsAll.modalOpen = false"
			:configurationPeers="bulkPeers"
		>
		</PeerJobsAllModal>
		<PeerJobsLogsModal
			key="PeerJobsLogsModal"
			v-if="configurationModals.peerScheduleJobsLogs.modalOpen" 
			@close="configurationModals.peerScheduleJobsLogs.modalOpen = false"
			:configurationInfo="configurationInfo">
		</PeerJobsLogsModal>
		<EditConfigurationModal
			key="EditConfigurationModal"
			@editRaw="configurationModals.editRawConfigurationFile.modalOpen = true"
			@close="configurationModals.editConfiguration.modalOpen = false"
			@dataChanged="(d) => configurationInfo = d"
			@refresh="fetchPeerList()"
			@backupRestore="configurationModals.backupRestore.modalOpen = true"
			@deleteConfiguration="configurationModals.deleteConfiguration.modalOpen = true"
			:configurationInfo="configurationInfo"
			v-if="configurationModals.editConfiguration.modalOpen">
		</EditConfigurationModal>
		<SelectPeersModal
			@refresh="fetchPeerList()"
			v-if="configurationModals.selectPeers.modalOpen"
			:configurationPeers="bulkPeers"
			@close="configurationModals.selectPeers.modalOpen = false"
		></SelectPeersModal>
		<PeerDetailsModal
			key="PeerDetailsModal"
			v-if="configurationModals.peerDetails.modalOpen"
			:selectedPeer="configurationPeers.find(x => x.id === configurationModalSelectedPeer.id) || configurationModalSelectedPeer"
			@close="configurationModals.peerDetails.modalOpen = false"
		>
		</PeerDetailsModal>
	</TransitionGroup>
	<div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mt-3 mb-5" v-if="filteredPeers > 0">
		<small class="text-muted">Showing {{(peerPage - 1) * peerPageSize + 1}}–{{Math.min(peerPage * peerPageSize, filteredPeers)}} of {{filteredPeers}} matching peers</small>
		<div class="btn-group" role="group" aria-label="Peer pages">
			<button class="btn btn-sm btn-outline-secondary" :disabled="peerPage <= 1" @click="gotoPeerPage(peerPage - 1)">Previous</button>
			<button class="btn btn-sm btn-outline-secondary" disabled>Page {{peerPage}} / {{pageCount}}</button>
			<button class="btn btn-sm btn-outline-secondary" :disabled="peerPage >= pageCount" @click="gotoPeerPage(peerPage + 1)">Next</button>
		</div>
	</div>
</div>
</template>

<style scoped>
.peerNav .nav-link{
	&.active{
		background-color: #efefef;
	}
}

th, td{
	background-color: transparent !important;
}

@media screen and (max-width: 576px) {
	.titleBtn{
		flex-basis: 100%;
	}
}
</style>