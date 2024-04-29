<template lang="html">
    <div class="row">
        <div class="col-sm-12">
            <div class="col-md-12">
                <div class="row">
                    <div class="panel panel-default">
                        <div class="panel-heading">
                            <h3 class="panel-title">{{ customerLabel }}
                                <a class="panelClicker" :href="'#' + detailsBody" data-toggle="collapse"
                                    data-parent="#userInfo" expanded="true" :aria-controls="detailsBody">
                                    <span class="glyphicon glyphicon-chevron-up pull-right "></span>
                                </a>
                            </h3>
                        </div>
                        <!--div v-if="applicantType == 'ORG'" class="panel-body panel-collapse collapse in" :id="detailsBody">
                                      <form class="form-horizontal">
                                          <div class="form-group">
                                            <label for="" class="col-sm-3 control-label">Name</label>
                                            <div class="col-sm-6">
                                                <input disabled type="text" class="form-control" name="applicantName" placeholder="" v-model="proposal.org_applicant.name" style="width: 100%">
                                            </div>
                                          </div>
                                          <div class="form-group">
                                            <label for="" class="col-sm-3 control-label">Trading Name</label>
                                            <div class="col-sm-6">
                                                <input disabled type="text" class="form-control" name="applicantName" placeholder="" v-model="proposal.org_applicant.trading_name" style="width: 100%">
                                            </div>
                                          </div>
                                          <div class="form-group">
                                            <label for="" class="col-sm-3 control-label" >ABN/ACN</label>
                                            <div class="col-sm-6">
                                                <input disabled type="text" class="form-control" name="applicantABN" placeholder="" v-model="proposal.org_applicant.abn" style="width: 100%">
                                            </div>
                                          </div>

                                      </form>
                                </div-->
                        <div v-if="applicantType == 'SUB'" class="panel-body panel-collapse collapse in" :id="detailsBody">
                            <form class="form-horizontal">
                                <div class="form-group">
                                    <label for="" class="col-sm-3 control-label">Given Name(s)</label>
                                    <div class="col-sm-6">
                                        <input disabled type="text" class="form-control" name="applicantName" placeholder=""
                                            v-model="applicant_first_name">
                                    </div>
                                </div>
                                <div class="form-group">
                                    <label for="" class="col-sm-3 control-label">Surname</label>
                                    <div class="col-sm-6">
                                        <input disabled type="text" class="form-control" name="applicantName" placeholder=""
                                            v-model="applicant_last_name">
                                    </div>
                                </div>
                                <div class="form-group">
                                    <label for="" class="col-sm-3 control-label">Date of Birth</label>
                                    <div class="col-sm-3 input-group date" ref="dobDatePicker">
                                        <input disabled type="text" class="form-control text-left ml-1" placeholder="DD/MM/YYYY" />
                                        <span class="input-group-addon">
                                            <span class="glyphicon glyphicon-calendar ml-1"></span>
                                        </span>
                                    </div>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-12">
                <div class="row">
                    <div class="panel panel-default">
                        <div class="panel-heading">
                            <h3 class="panel-title">Address Details
                                <a class="panelClicker" :href="'#' + addressBody" data-toggle="collapse"
                                    data-parent="#userInfo" expanded="false" :aria-controls="addressBody">
                                    <span class="glyphicon glyphicon-chevron-down pull-right "></span>
                                </a>
                            </h3>
                        </div>
                        <!--div v-if="applicantType == 'ORG' && proposal.org_applicant.address" class="panel-body panel-collapse collapse" :id="addressBody">
                                      <form class="form-horizontal">
                                          <div class="form-group">
                                            <label for="" class="col-sm-3 control-label">Street</label>
                                            <div class="col-sm-6">
                                                <input disabled type="text" class="form-control" name="street" placeholder="" v-model="proposal.org_applicant.address.line1">
                                            </div>
                                          </div>
                                          <div class="form-group">
                                            <label for="" class="col-sm-3 control-label" >Town/Suburb</label>
                                            <div class="col-sm-6">
                                                <input disabled type="text" class="form-control" name="surburb" placeholder="" v-model="proposal.org_applicant.address.locality">
                                            </div>
                                          </div>
                                          <div class="form-group">
                                            <label for="" class="col-sm-3 control-label">State</label>
                                            <div class="col-sm-2">
                                                <input disabled type="text" class="form-control" name="country" placeholder="" v-model="proposal.org_applicant.address.state">
                                            </div>
                                            <label for="" class="col-sm-2 control-label">Postcode</label>
                                            <div class="col-sm-2">
                                                <input disabled type="text" class="form-control" name="postcode" placeholder="" v-model="proposal.org_applicant.address.postcode">
                                            </div>
                                          </div>
                                          <div class="form-group">
                                            <label for="" class="col-sm-3 control-label" >Country</label>
                                            <div class="col-sm-4">
                                                <input disabled type="text" class="form-control" name="country" v-model="proposal.org_applicant.address.country"/>
                                            </div>
                                          </div>
                                       </form>
                                </div-->
                        <div class="panel-body panel-collapse collapse" :id="addressBody"> 
                            <form class="form-horizontal" action="index.html" method="post">
                                <alert v-if="showAddressError" type="danger" style="color:red">
                                    <div v-for="item in errorListAddress"><strong>{{ item }}</strong></div>
                                </alert>
                                <div class="address-box">
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">Residential Address</label>
                                        <div class="col-sm-6">
                                            <input :readonly="readonly" type="text" class="form-control" id="line1"
                                                name="Street" placeholder="" 
                                                v-model="residential_line1">
                                        </div>
                                    </div>
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">Town/Suburb</label>
                                        <div class="col-sm-6">
                                            <input :readonly="readonly" type="text" class="form-control" id="locality"
                                                name="Town/Suburb" placeholder=""
                                                v-model="residential_locality">
                                        </div>
                                    </div>
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">State</label>
                                        <div class="col-sm-3">
                                            <input :readonly="readonly" type="text" class="form-control" id="state"
                                                name="State" placeholder="" 
                                                v-model="residential_state">
                                        </div>
                                        <label for="" class="col-sm-1 control-label">Postcode</label>
                                        <div class="col-sm-2">
                                            <input :readonly="readonly" type="text" class="form-control" id="postcode"
                                                name="Postcode" placeholder=""
                                                v-model="residential_postcode">
                                        </div>
                                    </div>
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">Country</label>
                                        <div class="col-sm-4">
                                            <select :disabled="readonly" class="form-control" id="country" name="Country"
                                                v-model="residential_country">
                                                <option v-for="c in countries" :value="c.code">{{ c.name }}</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>
                                <!-- -->
                                <div class="form-group" />
                                <div class="address-box">
                                    <div class="form-group">
                                        <div class="col-sm-3">
                                        </div>
                                        <div class="col-sm-6">
                                            <input :disabled="readonly" type="checkbox" id="postal_same_as_residential"
                                                v-model="postal_same_as_residential">
                                            <label for="postal_same_as_residential" class="control-label">Same as
                                                residential address</label>
                                        </div>
                                    </div>
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">Postal Address</label>
                                        <div class="col-sm-6">
                                            <input :readonly="postalAddressReadonly" type="text" class="form-control"
                                                id="postal_line1" name="Street" placeholder=""
                                                v-model="postal_line1" />
                                        </div>
                                    </div>
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">Town/Suburb</label>
                                        <div class="col-sm-6">
                                            <input :readonly="postalAddressReadonly" type="text" class="form-control"
                                                id="postal_locality" name="Town/Suburb" placeholder=""
                                                v-model="postal_locality" />
                                        </div>
                                    </div>
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">State</label>
                                        <div class="col-sm-3">
                                            <input :readonly="postalAddressReadonly" type="text" class="form-control"
                                                id="postal_state" name="State" placeholder=""
                                                v-model="postal_state" />
                                        </div>
                                        <label for="" class="col-sm-1 control-label">Postcode</label>
                                        <div class="col-sm-2">
                                            <input :readonly="postalAddressReadonly" type="text" class="form-control"
                                                id="postal_postcode" name="Postcode" placeholder=""
                                                v-model="postal_postcode" />
                                        </div>
                                    </div>
                                    <div class="form-group">
                                        <label for="" class="col-sm-3 control-label">Country</label>
                                        <div class="col-sm-4">
                                            <select :disabled="postalAddressReadonly" class="form-control"
                                                id="postal_country" name="Country"
                                                v-model="postal_country">
                                                <option v-for="c in countries" :value="c.code">{{ c.name }}</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>

                                <div class="form-group">
                                    <div v-if="!readonly" class="col-sm-12">
                                        <button v-if="!updatingAddress" class="pull-right btn btn-primary"
                                            @click.prevent="updateAddress()">Update</button>
                                        <button v-else disabled class="pull-right btn btn-primary"><i
                                                class="fa fa-spin fa-spinner"></i>&nbsp;Updating</button>
                                    </div>
                                </div>
                            </form>

                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-12">
                <div class="row">
                    <div class="panel panel-default">
                        <div class="panel-heading">
                            <h3 class="panel-title">Contact Details
                                <a class="panelClicker" :href="'#' + contactsBody" data-toggle="collapse"
                                    data-parent="#userInfo" expanded="false" :aria-controls="contactsBody">
                                    <span class="glyphicon glyphicon-chevron-down pull-right "></span>
                                </a>
                            </h3>
                        </div>
                        <!--div v-if="applicantType == 'ORG'" class="panel-body panel-collapse collapse" :id="contactsBody">
                                    <table ref="contacts_datatable" :id="contacts_table_id" class="hover table table-striped table-bordered dt-responsive" cellspacing="0" width="100%">
                                    </table>
                                </div-->
                        <div v-if="applicantType == 'SUB'" class="panel-body panel-collapse collapse" :id="contactsBody">
                            <form class="form-horizontal">
                                <div class="form-group">
                                    <label for="" class="col-sm-3 control-label">Phone (work)</label>
                                    <div class="col-sm-6">
                                        <input disabled type="text" class="form-control" name="applicantPhoneNumber"
                                            placeholder="" v-model="contact_phone_number">
                                    </div>
                                </div>
                                <div class="form-group">
                                    <label for="" class="col-sm-3 control-label">Mobile</label>
                                    <div class="col-sm-6">
                                        <input disabled type="text" class="form-control" name="applicantMobileNumber"
                                            placeholder="" v-model="contact_mobile_number">
                                    </div>
                                </div>
                                <div class="form-group">
                                    <label for="" class="col-sm-3 control-label">Email</label>
                                    <div class="col-sm-6">
                                        <input disabled type="text" class="form-control" name="applicantEmail"
                                            placeholder="" v-model="email_user.email">
                                    </div>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-12" v-if="showElectoralRoll">
                <div class="row">
                    <div class="panel panel-default">
                        <div class="panel-heading">
                            <h3 class="panel-title">WA State Electoral Roll
                                <a class="panelClicker" :href="'#' + electoralRollBody" data-toggle="collapse"
                                    data-parent="#userInfo" expanded="false" :aria-controls="electoralRollBody">
                                    <span class="glyphicon glyphicon-chevron-down pull-right "></span>
                                </a>
                            </h3>
                        </div>

                        <div class="panel-body panel-collapse collapse" :id="electoralRollBody">
                            <form class="form-horizontal">
                                <div class="form-group">
                                    <div class="col-sm-8 mb-3">
                                        <strong>
                                            You must be on the WA state electoral roll to make an application
                                        </strong>
                                    </div>
                                    <div class="col-sm-8">
                                        <input :disabled="readonly" type="radio" id="electoral_roll_yes" :value="false"
                                            v-model="silentElector" />
                                        <label for="electoral_roll_yes">
                                            Yes, I am on the
                                            <a href="/" @click.prevent="uploadProofElectoralRoll">WA state electoral
                                                roll</a>
                                        </label>
                                    </div>
                                    <div class="col-sm-8">
                                        <input :disabled="readonly" class="mb-3" type="radio" id="electoral_roll_silent"
                                            :value="true" v-model="silentElector" />
                                        <label for="electoral_roll_silent">
                                            I am a silent elector
                                        </label>
                                        <div v-if="silentElector === true">
                                            <FileField :readonly="readonly" headerCSS="ml-3" label="Provide evidence"
                                                ref="electoral_roll_documents" name="electoral-roll-documents"
                                                :isRepeatable="true" :documentActionUrl="electoralRollDocumentUrl"
                                                :replace_button_by_text="true" />
                                        </div>
                                    </div>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>

        </div>
        <!-- <Assessment :proposal="proposal" :assessment="proposal.assessor_assessment" :hasAssessorMode="hasAssessorMode" :is_internal="is_internal" :is_referral="is_referral"></Assessment> -->
    </div>
</template>

<script>
//import Assessment from './assessment.vue'
//import FormSection from '@/components/forms/section_toggle.vue'
import FileField from '@/components/forms/filefield_immediate.vue'
import {
    api_endpoints,
    helpers
}
    from '@/utils/hooks'
export default {
    name: 'Applicant',
    //props:["type","name","id", "comment_value","value","isRequired","help_text","help_text_assessor","assessorMode","label","readonly","assessor_readonly", "help_text_url", "help_text_assessor_url"],
    props: {
        //proposal:{
        //    type: Object,
        //    required: true,
        //},
        email_user: {
            type: Object,
            required: true,
        },
        applicantType: {
            type: String,
            required: true,
        },
        customerType: {
            type: String,
            required: false,
        },
        showElectoralRoll: {
            type: Boolean,
            default: false
        },
        storedSilentElector: {
            type: Boolean,
        },
        proposalId: {
            type: Number,
        },
        proposal: {
            type: Object,
            default: {},
        },
    },
    data: function () {
        let vm = this;
        return {
            electoralRollSectionIndex: 'electoral_roll_' + vm._uid,
            silentElector: null,
            readonly: true,
            values: null,
            countries: [],
            showAddressError: false,
            detailsBody: 'detailsBody' + vm._uid,
            addressBody: 'addressBody' + vm._uid,
            contactsBody: 'contactsBody' + vm._uid,
            electoralRollBody: 'electoralRollBody' + vm._uid,
            panelClickersInitialised: false,
            contacts_table_id: vm._uid + 'contacts-table',
            contacts_table_initialised: false,
            contacts_options: {
                language: {
                    processing: "<i class='fa fa-4x fa-spinner fa-spin'></i>"
                },
                responsive: true,
                ajax: {
                    "url": vm.contactsURL,
                    "dataSrc": ''
                },
                columns: [
                    {
                        title: 'Name',
                        mRender: function (data, type, full) {
                            return full.first_name + " " + full.last_name;
                        }
                    },
                    {
                        title: 'Phone',
                        data: 'phone_number'
                    },
                    {
                        title: 'Mobile',
                        data: 'mobile_number'
                    },
                    {
                        title: 'Fax',
                        data: 'fax_number'
                    },
                    {
                        title: 'Email',
                        data: 'email'
                    },
                ],
                processing: true
            },
            contacts_table: null,
        }
    },
    components: {
        FileField,
        //FormSection,
        //Assessment
    },
    // filters: {
    //     moment: function (date) {
    //         return moment(date).format('DD/MM/YYYY');
    //     }
    // },
    computed: {
        applicant_first_name: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.first_name
            } else {
                return this.email_user.first_name
            }
        },
        applicant_last_name: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.last_name
            } else {
                return this.email_user.last_name
            }
        },
        applicant_dob: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.dob
            } else {
                return this.email_user.dob
            }
        },
        contact_mobile_number: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.mobile_number
            } else {
                return this.email_user.mobile_number
            }
        },
        contact_phone_number: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.phone_number
            } else {
                return this.email_user.phone_number
            }
        },
        residential_line1: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.residential_line1
            } else {
                return this.email_user.residential_address.line1
            }
        },
        residential_locality: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.residential_locality
            } else {
                return this.email_user.residential_address.locality
            }
        },
        residential_state: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.residential_state
            } else {
                return this.email_user.residential_address.state
            }
        },
        residential_postcode: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.residential_postcode
            } else {
                return this.email_user.residential_address.postcode
            }
        },
        residential_country: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.residential_country
            } else {
                return this.email_user.residential_address.country
            }
        },
        postal_same_as_residential: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.postal_same_as_residential
            } else {
                return this.email_user.postal_same_as_residential
            }
        },
        postal_line1: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.postal_line1
            } else {
                return this.email_user.postal_address.line1
            }
        },
        postal_locality: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.postal_locality
            } else {
                return this.email_user.postal_address.locality
            }
        },
        postal_state: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.postal_state
            } else {
                return this.email_user.postal_address.state
            }
        },
        postal_postcode: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.postal_postcode
            } else {
                return this.email_user.postal_address.postcode
            }
        },
        postal_country: function(){
            if (this.proposal && this.proposal.proposal_applicant){
                return this.proposal.proposal_applicant.postal_country
            } else {
                return this.email_user.postal_address.country
            }
        },
        electoralRollDocumentUrl: function () {
            let url = '';
            //if (this.profile && this.profile.id) {
            if (this.proposalId) {
                //url = helpers.add_endpoint_join(
                //    '/api/proposal/',
                //    this.proposalId + '/process_electoral_roll_document/'
                //)
                url = '/api/proposal/' + this.proposal.id + '/electoral_roll_document/'
            }
            return url;
        },

        postalAddressReadonly: function () {
            /*
            if (this.readonly || this.email_user.postal_same_as_residential) {
                return true;
            }
            */
            return true;
        },
        contactsURL: function () {
            // We don't need anything relating to organisations
            //return this.proposal != null ? helpers.add_endpoint_json(api_endpoints.organisations, this.proposal.org_applicant.id+'/contacts') : '';
            return ''
        },
        customerLabel: function () {
            let label = 'Applicant';
            if (this.customerType && this.customerType === 'holder') {
                label = 'Holder';
            }
            return label;
        },

        //applicantType: function(){
        //    return this.proposal.applicant_type;
        //},
        // hasAssessorMode:function(){
        //     return this.proposal && this.proposal.assessor_mode.has_assessor_mode ? true : false;
        // },
    },
    methods: {
        addEventListeners: function () {
            let vm = this;
            let elDob = $(vm.$refs.dobDatePicker);

            let options = {
                format: "DD/MM/YYYY",
                showClear: true,
                useCurrent: false,
                maxDate: moment(),
                // defaultDate: moment(vm.email_user.dob, 'YYYY-MM-DD'),
                defaultDate: moment(vm.applicant_dob, 'YYYY-MM-DD'),
            };

            elDob.datetimepicker(options);

            elDob.on("dp.change", function (e) {
                let selected_date = null;
                if (e.date) {
                    // Date selected
                    selected_date = e.date.format('DD/MM/YYYY')  // e.date is moment object
                    vm.email_user.dob = selected_date;
                    //elDob.data('DateTimePicker').maxDate(true);
                } else {
                    // Date not selected
                    vm.email_user.dob = selected_date;
                    //elDob.data('DateTimePicker').maxDate(false);
                }
            });
        },
        fetchCountries: function () {
            let vm = this;
            //vm.loading.push('fetching countries');
            vm.$http.get(api_endpoints.countries).then((response) => {
                vm.countries = response.body;
                //vm.loading.splice('fetching countries',1);
            }, (response) => {
                //console.log(response);
                //vm.loading.splice('fetching countries',1);
            });
        },

        initialiseOrgContactTable: function () {
            let vm = this;
            //console.log("i am here")
            //if (vm.proposal && !vm.contacts_table_initialised){
            if (!vm.contacts_table_initialised) {
                // We don't need anything relating to organisations
                //vm.contacts_options.ajax.url = helpers.add_endpoint_json(api_endpoints.organisations, vm.proposal.org_applicant.id + '/contacts');
                vm.contacts_table = $('#' + vm.contacts_table_id).DataTable(vm.contacts_options);
                vm.contacts_table_initialised = true;
            }
        },
    },
    mounted: function () {
        let vm = this;
        this.fetchCountries();
        if (!vm.panelClickersInitialised) {
            $('.panelClicker[data-toggle="collapse"]').on('click', function () {
                var chev = $(this).children()[0];
                window.setTimeout(function () {
                    $(chev).toggleClass("glyphicon-chevron-down glyphicon-chevron-up");
                }, 100);
            });
            vm.panelClickersInitialised = true;
        }
        this.$nextTick(() => {
            vm.initialiseOrgContactTable();
            vm.addEventListeners()
        });
        this.silentElector = this.storedSilentElector;
    }
}
</script>

<style lang="css" scoped></style>
