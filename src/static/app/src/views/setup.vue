<script>
import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";
import {fetchPost} from "@/utilities/fetch.js";
import LocaleText from "@/components/text/localeText.vue";
export default {
	name: "setup",
	components: {LocaleText},
	setup(){
		const store = DashboardConfigurationStore();
		return {store}
	},
	data(){
		return {
			setup: {
				username: "",
				newPassword: "",
				repeatNewPassword: "",
				enable_totp: true
			},
			remoteEndpoint: "",
			loading: false,
			errorMessage: "",
			done: false
		}
	},
	computed: {
        validEndpoint(){
            const host = this.remoteEndpoint.trim();
            // The port is automatically appended from each tunnel config.
            // Exclude schemes, whitespace, paths, and pasted ports.
            return host.length > 0 && host.length <= 253 &&
                /^(?:[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?|\\[[0-9a-fA-F:]+\\])$/.test(host) &&
                !host.includes("..");
        },
		goodToSubmit(){
			return this.setup.username 
				&& this.setup.newPassword.length >= 8
				&& this.setup.repeatNewPassword.length >= 8
				&& this.setup.newPassword === this.setup.repeatNewPassword
                && this.validEndpoint
		}
	},
	methods: {
        async submit(){
            if (!this.goodToSubmit || this.loading || this.done) return;
            this.loading = true;
            this.errorMessage = "";
            try {
                // Existing settings API, no extra Welcome_Finish fields.
                // This page is only reached by FIRST-TIME welcome flow;
                // upgrades/restores that completed setup are not prompted.
                let address = null;
                await fetchPost("/api/updateDashboardConfigurationItem", {
                    section:"Peers", key:"remote_endpoint",
                    value:this.remoteEndpoint.trim()
                }, res => {address = res;});
                if (!address?.status) throw new Error(address?.message || "Cannot save tunnel endpoint.");
                let welcome = null;
                await fetchPost("/api/Welcome_Finish", this.setup, res => {welcome = res;});
                if (!welcome?.status) throw new Error(welcome?.message || "Cannot complete initial setup.");
                this.done = true;
                this.$router.push("/2FASetup");
            } catch(e) {
                this.errorMessage = e?.message || "Initial setup failed.";
                document.querySelector(".login-container-fluid")?.scrollTo({top:0,behavior:"smooth"});
            } finally { this.loading = false; }
        }
	}
}
</script>

<template>
	<div class="container-fluid login-container-fluid d-flex main pt-5 overflow-scroll" 
	     :data-bs-theme="this.store.Configuration.Server.dashboard_theme">
		<div class="m-auto text-body" style="width: 500px">
			<span class="dashboardLogo display-4">
				<LocaleText t="Nice to meet you!"></LocaleText>
			</span>
			<p class="mb-5">
				<LocaleText t="Please fill in the following fields to finish setup"></LocaleText>
				😊</p>
			<div>
				<h3>
					<LocaleText t="Create an account"></LocaleText>
				</h3>
				<div class="alert alert-danger" v-if="this.errorMessage">
					{{this.errorMessage}}
				</div>
				<div class="d-flex flex-column gap-3">
					<form id="createAccount" class="d-flex flex-column gap-2">
						<div class="form-group text-body">
							<label for="username" class="mb-1 text-muted">
								<small>
									<LocaleText t="Enter an username you like"></LocaleText>
								</small></label>
							<input type="text"
							       autocomplete="username"
							       v-model="this.setup.username"
							       class="form-control" id="username" name="username" required>
						</div>
						<div class="form-group text-body">
							<label for="password" class="mb-1 text-muted">
								<small>
									<LocaleText t="Enter a password"></LocaleText>
									<code>
										<LocaleText t="(At least 8 characters and make sure is strong enough!)"></LocaleText>
									</code>
								</small>
							</label>
							<input type="password"
							       autocomplete="new-password"
							       v-model="this.setup.newPassword"
							       class="form-control" id="password" name="password" required>
						</div>
						<div class="form-group text-body">
							<label for="confirmPassword" class="mb-1 text-muted">
								<small>
									<LocaleText t="Confirm password"></LocaleText>
								</small>
							</label>
							<input type="password"
							       autocomplete="confirm-new-password"
							       v-model="this.setup.repeatNewPassword"
							       class="form-control" id="confirmPassword" name="confirmPassword" required>
                        </div>
							<div class="form-group text-body">
                            <label for="first-endpoint" class="mb-1 text-muted">
                                <small>Peer Remote Endpoint — public IP or tunnel hostname (without port)</small>
                            </label>
                            <input id="first-endpoint" type="text" class="form-control"
                                v-model.trim="remoteEndpoint" placeholder="vpn.example.com"
                                spellcheck="false" autocomplete="off" required>
                            <small class="text-muted">
                                Used in peer QR codes and downloadable configs. The server's
                                WireGuard listening port is added automatically.
                                Backups preserve this value on restores.
                            </small>
                            <div v-if="remoteEndpoint && !validEndpoint" class="small text-danger">
                                Enter a hostname or IP only, without a protocol or port.
                            </div>
                        </div>
					</form>
<!--					<div class="form-check form-switch">-->
<!--						<input class="form-check-input" type="checkbox" role="switch" id="enable_totp" -->
<!--						       v-model="this.setup.enable_totp">-->
<!--						<label class="form-check-label" -->
<!--						       for="enable_totp">Enable 2 Factor Authentication? <strong>Strongly recommended</strong></label>-->
<!--					</div>-->
<!--					<Suspense>-->
<!--						<Transition name="fade">-->
<!--							<Totp v-if="this.setup.enable_totp" @verified="this.setup.verified_totp = true"></Totp>-->
<!--						</Transition>-->
<!--					</Suspense>-->
					
					<button class="btn btn-dark btn-lg mb-5 d-flex btn-brand shadow align-items-center" 
					        ref="signInBtn"
					        :disabled="!this.goodToSubmit || this.loading || this.done" @click="this.submit()">
						<span class="d-flex align-items-center w-100" v-if="!this.loading && !this.done">
							<LocaleText t="Next"></LocaleText>
							
							<i class="bi bi-chevron-right ms-auto"></i></span>
						<span class="d-flex align-items-center w-100" v-else>
							<LocaleText t="Saving..."></LocaleText>
							<span class="spinner-border ms-auto spinner-border-sm" role="status">
							  <span class="visually-hidden">Loading...</span>
							</span></span>
					</button>
				</div>
			</div>
		</div>
	</div>
</template>

<style scoped>

</style>