import logging
from confy import env
from django.http import HttpResponse, Http404
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.generic import DetailView
from django.views.generic.base import TemplateView
# from django.conf import settings
from django.contrib.auth.mixins import UserPassesTestMixin, LoginRequiredMixin

from mooringlicensing import settings
from mooringlicensing.helpers import is_internal, is_customer
from mooringlicensing.forms import *
from mooringlicensing.components.approvals.models import Approval, DcvAdmission, DcvPermit
from mooringlicensing.components.proposals.models import (
        Proposal, 
        HelpPage
        )
from mooringlicensing.components.compliances.models import Compliance
from django.core.management import call_command
import os
import mimetypes


logger = logging.getLogger(__name__)

class InternalView(UserPassesTestMixin, TemplateView):
    template_name = 'mooringlicensing/dash/index.html'

    def test_func(self):
        return is_internal(self.request)

    def get_context_data(self, **kwargs):
        context = super(InternalView, self).get_context_data(**kwargs)
        context['dev'] = settings.DEV_STATIC
        context['dev_url'] = settings.DEV_STATIC_URL
        if hasattr(settings, 'DEV_APP_BUILD_URL') and settings.DEV_APP_BUILD_URL:
            context['app_build_url'] = settings.DEV_APP_BUILD_URL
        return context


class ExternalView(LoginRequiredMixin, TemplateView):
    template_name = 'mooringlicensing/dash/index.html'

    def get_context_data(self, **kwargs):
        logger.info(f'Getting context in the ExternalView...')
        context = super(ExternalView, self).get_context_data(**kwargs)
        context['dev'] = settings.DEV_STATIC
        context['dev_url'] = settings.DEV_STATIC_URL
        if hasattr(settings, 'DEV_APP_BUILD_URL') and settings.DEV_APP_BUILD_URL:
            context['app_build_url'] = settings.DEV_APP_BUILD_URL
        return context


class ExternalProposalView(DetailView):
    model = Proposal
    template_name = 'mooringlicensing/dash/index.html'

class ExternalComplianceView(DetailView):
    model = Compliance
    template_name = 'mooringlicensing/dash/index.html'

class InternalComplianceView(DetailView):
    model = Compliance
    template_name = 'mooringlicensing/dash/index.html'


class MooringLicensingRoutingView(TemplateView):
    template_name = 'mooringlicensing/index.html'

    def get(self, *args, **kwargs):
        # if self.request.user.is_authenticated():
        if self.request.user.is_authenticated:
            if is_internal(self.request):
                return redirect('internal')
            return redirect('external')
        kwargs['form'] = LoginForm
        return super(MooringLicensingRoutingView, self).get(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(MooringLicensingRoutingView, self).get_context_data(**kwargs)
        daily_admission_page_url = env('DAILY_ADMISSION_PAGE_URL', '')
        context.update({
            'daily_admission_page_url': daily_admission_page_url
        })
        return context


class MooringLicensingContactView(TemplateView):
    template_name = 'mooringlicensing/contact.html'


class MooringLicensingFurtherInformationView(TemplateView):
    template_name = 'mooringlicensing/further_info.html'


class InternalProposalView(DetailView):
    model = Proposal
    template_name = 'mooringlicensing/dash/index.html'

    def get(self, *args, **kwargs):
        if self.request.user.is_authenticated:
            if is_internal(self.request):
                return super(InternalProposalView, self).get(*args, **kwargs)
            return redirect('external-proposal-detail')
        kwargs['form'] = LoginForm
        return super(MooringLicensingRoutingDetailView, self).get(*args, **kwargs)


# @login_required(login_url='ds_home')
@login_required(login_url='home')
def first_time(request):
    context = {}
    if request.method == 'POST':
        form = FirstTimeForm(request.POST)
        redirect_url = form.data['redirect_url']
        if not redirect_url:
            redirect_url = '/'
        if form.is_valid():
            # set user attributes
            request.user.first_name = form.cleaned_data['first_name']
            request.user.last_name = form.cleaned_data['last_name']
            request.user.dob = form.cleaned_data['dob']
            request.user.save()
            return redirect(redirect_url)
        context['form'] = form
        context['redirect_url'] = redirect_url
        return render(request, 'mooringlicensing/user_profile.html', context)
    # GET default
    if 'next' in request.GET:
        context['redirect_url'] = request.GET['next']
    else:
        context['redirect_url'] = '/'
    context['dev'] = settings.DEV_STATIC
    context['dev_url'] = settings.DEV_STATIC_URL
    return render(request, 'mooringlicensing/dash/index.html', context)


class HelpView(LoginRequiredMixin, TemplateView):
    template_name = 'mooringlicensing/help.html'

    def get_context_data(self, **kwargs):
        context = super(HelpView, self).get_context_data(**kwargs)

        if self.request.user.is_authenticated:
            application_type = kwargs.get('application_type', None)
            if kwargs.get('help_type', None)=='assessor':
                if is_internal(self.request):
                    qs = HelpPage.objects.filter(application_type__name__icontains=application_type, help_type=HelpPage.HELP_TEXT_INTERNAL).order_by('-version')
                    context['help'] = qs.first()
            else:
                qs = HelpPage.objects.filter(application_type__name__icontains=application_type, help_type=HelpPage.HELP_TEXT_EXTERNAL).order_by('-version')
                context['help'] = qs.first()
        return context


class LoginSuccess(TemplateView):
    template_name = 'mooringlicensing/login_success.html';

    def get(self, request, *args, **kwargs):
        context = {'LEDGER_UI_URL' : settings.LEDGER_UI_URL}
        response = render(request, self.template_name, context)
        return response


class ManagementCommandsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'mooringlicensing/mgt-commands.html'

    def test_func(self):
        return is_internal(self.request)

    def get(self, request, *args, **kwargs):
        debug = request.GET.get('debug', 'f')
        if debug.lower() in ['true', 't', 'yes', 'y']:
            debug = True
        else:
            debug = False

        context = self.get_context_data(**kwargs)
        context.update({'debug': debug})
        return self.render_to_response(context)

    def post(self, request):
        data = {}
        command_script = request.POST.get('script', None)

        if command_script:
            logger.info('Running {}...'.format(command_script))
            call_command(command_script,)
            data.update({command_script: 'true'})

        return render(request, self.template_name, data)

def is_authorised_to_access_proposal_document(request,document_id):
    if is_internal(request):
        return True
    elif is_customer(request):
        user = request.user
        return Proposal.objects.filter(id=document_id).filter(submitter=user.id).exists()
    
def is_authorised_to_access_approval_document(request,document_id):
    if is_internal(request):
        return True
    elif is_customer(request):
        user = request.user
        return Approval.objects.filter(id=document_id).filter(submitter=user.id).exists()
    
def is_authorised_to_access_dcv_admission_document(request,document_id):
    if is_internal(request):
        return True
    elif is_customer(request):
        user = request.user
        return DcvAdmission.objects.filter(id=document_id).filter(submitter=user.id).exists()
    
def is_authorised_to_access_dcv_permit_document(request,document_id):
    if is_internal(request):
        return True
    elif is_customer(request):
        user = request.user
        return DcvPermit.objects.filter(id=document_id).filter(submitter=user.id).exists()
    
def get_file_path_id(check_str,file_path):
    file_name_path_split = file_path.split("/")
    #if the check_str is in the file path, the next value should be the id
    if check_str in file_name_path_split:
        id_index = file_name_path_split.index(check_str)+1
        if len(file_name_path_split) > id_index and file_name_path_split[id_index].isnumeric():
            return int(file_name_path_split[id_index])
        else:
            return False
    else:
        return False

def is_authorised_to_access_document(request):

    if is_internal(request):
        return True
    elif is_customer(request):
        p_document_id = get_file_path_id("proposals",request.path) or get_file_path_id("proposal",request.path)
        if p_document_id:
            return is_authorised_to_access_proposal_document(request,p_document_id)
        a_document_id = get_file_path_id("approvals",request.path) or get_file_path_id("approval",request.path)
        if a_document_id:
            return is_authorised_to_access_approval_document(request,a_document_id)
        da_document_id = get_file_path_id("dcv_admission",request.path)
        if da_document_id:
            return is_authorised_to_access_dcv_admission_document(request,da_document_id)
        dp_document_id = get_file_path_id("dcv_permit",request.path)
        if dp_document_id:
            return is_authorised_to_access_dcv_permit_document(request,dp_document_id)
        return False
    else:
        return False

def getPrivateFile(request):

    if is_authorised_to_access_document(request):
        file_name_path =  request.path
        #norm path will convert any traversal or repeat / in to its normalised form
        full_file_path= os.path.normpath(settings.BASE_DIR+file_name_path) 
        #we then ensure the normalised path is within the BASE_DIR (and the file exists)
        if full_file_path.startswith(settings.BASE_DIR) and os.path.isfile(full_file_path):
            extension = file_name_path.split(".")[-1]
            the_file = open(full_file_path, 'rb')
            the_data = the_file.read()
            the_file.close()
            if extension == 'msg':
                return HttpResponse(the_data, content_type="application/vnd.ms-outlook")
            if extension == 'eml':
                return HttpResponse(the_data, content_type="application/vnd.ms-outlook")

            return HttpResponse(the_data, content_type=mimetypes.types_map['.'+str(extension)])

    return HttpResponse()