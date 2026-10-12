<script>
import ScheduleDropdown from "@/components/configurationComponents/peerScheduleJobsComponents/scheduleDropdown.vue";
import SchedulePeerJob from "@/components/configurationComponents/peerScheduleJobsComponents/schedulePeerJob.vue";
import {WireguardConfigurationsStore} from "@/stores/WireguardConfigurationsStore.js";
import {v4} from "uuid";
import LocaleText from "@/components/text/localeText.vue";
export default {
	name: "peerJobs",
	setup(){
		const store = WireguardConfigurationsStore();
		return {store}
	},
	props:{
		selectedPeer: Object
	},
	components:{
		LocaleText,
		SchedulePeerJob,
		ScheduleDropdown,
	},
	data(){
        return {
            localJobs: Array.isArray(this.selectedPeer?.jobs) ? [...this.selectedPeer.jobs] : [],
            addError: ""
        }
    },
	methods:{
		deleteJob(j){
			this.localJobs = this.localJobs.filter(x => x.JobID !== j.JobID);
		},
		addJob(){
            // Paginated/search results intentionally omit peer.configuration.
            // Resolve the parent configuration from the current page instead.
            const configuration = String(this.$route?.params?.id ||
                this.selectedPeer?.configuration?.Name || "");
            const dropdowns = this.store.PeerScheduleJobs?.dropdowns;
            if (!configuration || !this.selectedPeer?.id ||
                !dropdowns?.Field?.length || !dropdowns?.Operator?.length ||
                !dropdowns?.Action?.length) {
                this.addError = "Cannot determine this peer's configuration. Reload the peer page.";
                return;
            }
            this.addError = "";
            this.localJobs.unshift({
                JobID: v4(), Configuration: configuration, Peer: this.selectedPeer.id,
                Field: dropdowns.Field[0].value,
                Operator: dropdowns.Operator[0].value,
                Value: "", CreationDate: "", ExpireDate: null,
                Action: dropdowns.Action[0].value
            });

		}
	},
	
}
</script>

<template>
	<div class="peerSettingContainer w-100 h-100 position-absolute top-0 start-0 overflow-y-scroll">
		<div class="container d-flex h-100 w-100">
			<div class="m-auto modal-dialog-centered dashboardModal">
				<div class="card rounded-3 shadow" style="width: 700px">
					<div class="card-header bg-transparent d-flex align-items-center gap-2 border-0 p-4 pb-2">
						<h4 class="mb-0 fw-normal">
							<LocaleText t="Schedule Jobs"></LocaleText>
						</h4>
						<button type="button" class="btn-close ms-auto" @click="this.$emit('close')"></button>
					</div>
					<div class="card-body px-4 pb-4 pt-2 position-relative">
						<div v-if="addError" class="alert alert-warning small" role="alert">{{addError}}</div>
                        <div class="d-flex align-items-center mb-3">
							<button class="btn bg-primary-subtle border-1 border-primary-subtle text-primary-emphasis rounded-3 shadow" 
							        
							        type="button" @click="this.addJob()">
								<i class="bi bi-plus-lg me-2"></i>
								<LocaleText t="Job"></LocaleText>
							</button>
						</div>
						<TransitionGroup name="schedulePeerJobTransition" tag="div" class="position-relative">
							<SchedulePeerJob
								@refresh="this.$emit('refresh')"
								@delete="this.deleteJob(job)"
								:dropdowns="this.store.PeerScheduleJobs.dropdowns"
								:key="job.JobID"
								:pjob="job" v-for="job in this.localJobs">
							</SchedulePeerJob>
							
							<div class="card shadow-sm" key="none" 
							     style="height: 153px"
							     v-if="this.localJobs.length === 0">
								<div class="card-body text-muted text-center d-flex">
									<h6 class="m-auto">
										<LocaleText t="This peer does not have any job yet."></LocaleText>
									</h6>
								</div>
							</div>
						</TransitionGroup>
					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<style scoped>
.schedulePeerJobTransition-move, /* apply transition to moving elements */
.schedulePeerJobTransition-enter-active,
.schedulePeerJobTransition-leave-active {
	transition: all 0.4s cubic-bezier(0.82, 0.58, 0.17, 0.9);
}

.schedulePeerJobTransition-enter-from,
.schedulePeerJobTransition-leave-to {
	opacity: 0;
	transform: scale(0.9);
}

/* ensure leaving items are taken out of layout flow so that moving
   animations can be calculated correctly. */
.schedulePeerJobTransition-leave-active {
	position: absolute;
	width: 100%;
}
</style>