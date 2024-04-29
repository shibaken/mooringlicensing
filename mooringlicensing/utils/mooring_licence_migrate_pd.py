from ledger_api_client.ledger_models import EmailUserRO as EmailUser, Invoice, Address
from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.contrib.gis.geos import GEOSGeometry
from django.utils import timezone
from ledger_api_client.country_models import Country
import csv
import os
import datetime
import time
import string
import pandas as pd
import numpy as np
import requests
import json

from dateutil.parser import parse
from dateutil.relativedelta import relativedelta
from decimal import Decimal

from ledger_api_client.utils import get_or_create
from mooringlicensing.components.payments_ml.models import FeeSeason

from mooringlicensing.components.proposals.models import (
    Proposal,
    ProposalApplicant,
    ProposalType,
    Vessel,
    Owner,
    VesselOwnership,
    VesselDetails,
    MooringLicenceApplication,
    AuthorisedUserApplication,
    WaitingListApplication,
    AnnualAdmissionApplication,
    ProposalUserAction,
    Mooring,
    MooringBay,
)
from mooringlicensing.components.approvals.models import (
    Approval, 
    ApprovalHistory, 
    MooringLicence, 
    AuthorisedUserPermit,
    VesselOwnershipOnApproval, 
    MooringOnApproval,
    WaitingListAllocation,
    AnnualAdmissionPermit,
    ApprovalUserAction,
    Sticker,
    DcvOrganisation,
    DcvPermit,
    DcvVessel,
)

from tqdm import tqdm
import logging
logger = logging.getLogger(__name__)

NL = '\n'

TODAY = datetime.datetime.now(datetime.timezone.utc).date()
EXPIRY_DATE = datetime.date(2024,8,31)
START_DATE = datetime.date(2023,9,1)
DATE_APPLIED = '2023-09-01'
FEE_SEASON = '2023 - 2024'

#ApplicationType.objects.all().values_list('code')
#Out[26]: <QuerySet [('wla',), ('aaa',), ('aua',), ('mla',), ('dcvp',), ('dcv',), ('replacement_sticker',), ('mooring_swap',)]>

VESSEL_TYPE_MAPPING = {
    'A': 'Catamaran',
    'B': 'Bow Rider',
    'C': 'Cabin Cruiser',
    'E': 'Centre Console',
    'F': 'Ferry',
    'G': 'Rigid Inflatable',
    'H': 'Half Cabin',
    'I': 'Inflatable',
    'L': 'Launch',
    'M': 'Motor Sailer',
    'O': 'Open Boat',
    'P': 'Power Boat',
    'R': 'Runabout',
    'S': 'Fishing Boat',
    'T': 'Tender',
    'W': 'Walkaround',
    'Y': 'Yacht',
}

USER_COLUMN_MAPPING = {
    'Person No':                        'pers_no',
    'Type of Licences':                 'licences_type', 
    'Cancelled':                        'cancelled', 
    'Paid Up?':                         'paid_up', 
    'BP$':                              'bp',
    'First Name':                       'first_name', 
    'Middle1 Initial':                  'middle_name', 
    'Last Name':                        'last_name', 
    'Age':                              'age', 
    'Date of Birth':                    'dob', 
    'Company':                          'company', 
    'Address':                          'address', 
    'Suburb':                           'suburb',
    'State':                            'state', 
    'Post Code':                        'postcode', 
    'Postal Address':                   'postal_address',
    'Postal Suburb':                    'postal_suburb', 
    'Postal State':                     'postal_state', 
    'Postal Post Code':                 'postal_postcode',
    'Electoral Roll':                   'electoral_role', 
    'Phone Home':                       'home_number', 
    'Phone Work':                       'work_number',
    'Phone Mobile':                     'mobile_number', 
    'Fax':                              'fax', 
    'e-Mail Address':                   'email',
    'Email Promotions':                 'email_prom', 
    'Waitlist Bay':                     'wl_bay', 
    'Bays Auth for':                    'bays_auth_for', 
    'No Bays Auth for':                 'no_bays_auth_for', 
    'Vessel Sold':                      'vessel_sold',
    'Current Mooring No':               'current_mooring_no', 
    'Vsl Due Date':                     'ves_due_date', 
    'No Users (for Lics)':              'no_user_lics',
}


ML_COLUMN_MAPPING = {
    'Mooring Number':                   'mooring_no', 
    'Licencee First Name':              'first_name_l',
    'Licencee Last Name':               'last_name_l',
    'Pers No':                          'pers_no',
    'Grandfather':                      'grandfather',
    'Tonnage':                          'tonnage',
    'Cert for addn Vessel':             'cert_addn_vessel',
    'Mooring Type':                     'mooring_type',
    'Float Colour':                     'float_colour',
    'Enviro Specs':                     'enviro_specs',
    'Buoy Specs':                       'buoy_specs',
    'Mooring Bay':                      'mooring_bay',
    'Cancelled':                        'cancelled',
    'No Auth Users  2012':              'no_auth_user_2012',
    'No Auth Users  2012 RIA':          'no_auth_users_2012_ria',
    'No Auth Users  2012 Lic':          'no_auth_user_2012_lic',
    'No Auth Users':                    'no_auth_users',
    'No Auth Users Lic':                'no_auth_user_lic',
    'No Auth Users XL':                 'no_auth_users_xl',
    'Colour Current':                   'colour',
    'Current Max Vessel on Site':       'max_vessel_on_site',
    'Lic Ves Length':                   'ves_length_lic',
    'Draft':                            'draft',
    'Max Ves Length':                   'max_ves_length',
    'Comments/Notes':                   'comments_notes',
    'Comments':                         'comments',
}

AU_COLUMN_MAPPING = {
    'Mooring Number':                   'mooring_no', 
    'Cancelled':                        'cancelled',
    'Licencee Approved Authorised User':'licencee_approved',
    'First Name (L)':                   'first_name_l',
    'Last Name (L)':                    'last_name_l',
    'Person No (L)':                    'pers_no_l',
    'User Type':                        'user_type',
    'Date Issued':                      'date_issued',
    'Sticker No':                       'sticker',
    'First Name (U)':                   'first_name_u',
    'Last Name (U)':                    'last_name_u',
    'Lic of Mooring No':                'lic_of_mooring_no',
    'PersNo (U)':                       'pers_no_u',
    'Vessel Rego':                      'vessel_rego',
    'Vessel Name':                      'vessel_name',
    'Vessel Length':                    'vessel_length',
    'Vessel Draft':                     'vessel_draft',
    'Date Cancelled':                   'date_cancelled',
    'Reason Cancelled':                 'reason_cancelled',
}

WL_COLUMN_MAPPING = {
    'FirstName':                        'first_name',
    'LastName':                         'last_name',
    'PersNo':                           'pers_no',
    'TrimNo':                           'trim_no',
    'Bay':                              'bay',
    'AppStatus':                        'app_status',
    'BayPosNo':                         'bay_pos_no',
    'SeqNo':                            'seq_no',
    'VesName':                          'vessel_name',
    'RegLength':                        'vessel_length',
    'VesRego':                          'vessel_rego',
    'Draft':                            'vessel_draft',
    'DateApplied':                      'date_applied',
    'DateCancelled':                    'date_cancelled',
    'DateAllocated':                    'date_allocated',
    'DateAnnConfRecd':                  'date_annconf_recd',
    'MooringNo':                        'mooring_no',
    'Cancelled':                        'cancelled',
    'Comment':                          'comment',
}


AA_COLUMN_MAPPING = {
    'ID':                               'id',
    'Date Created':                     'date_created',
    'First Name':                       'first_name',
    'Last Name':                        'last_name',
    'Postal Address 1':                 'address',
    'Suburb':                           'suburb',
    'State':                            'state',
    'Post Code':                        'postcode',
    'Sticker No':                       'sticker_no',
    'Vessel Rego':                      'rego_no',
    'Email':                            'email',
    'Mobile':                           'mobile_number',
    'Phone':                            'work_number',
    'Vessel Name':                      'vessel_name',
    'Vessel Length':                    'vessel_length',
    'Year':                             'year',
    'Status':                           'status',
    'Booking Period':                   'booking_period',
    'Country':                          'country',
}



def get_user_as_email_user(sender):
    try:
        sender_user = EmailUser.objects.get(email__icontains=sender)
    except:
        EmailUser.objects.create(email=sender, password='')
        sender_user = EmailUser.objects.get(email__icontains=sender)
    return sender_user


class MooringLicenceReader():
    """
    First need to run clean().
    This will write output files to /var/www/mooringlicensing/mooringlicensing/utils/csv/clean
        from mooringlicensing.utils.export_clean import clean
        clean()
        
    Notes:
    Clean CSV
             1. open vessel_details in vi and replace the str chars --> ':%s/"//g'
             2. vessel_details - 'Person No' 210406 - has extra column delete cell B

    Manual Clean:
             3. For Mooring Booking, convert the downloaded file Moorings-RIA to pip-delimited (annual_admissions_booking_report.csv)
        
    FROM shell_plus:
        from mooringlicensing.utils.mooring_licence_migrate_pd import MooringLicenceReader
        mlr=MooringLicenceReader('PersonDets.txt', 'MooringDets.txt', 'VesselDets.txt', 'UserDets.txt', 'ApplicationDets.txt', 'annual_admissions_booking_report.csv')

        mlr.create_users()
        mlr.create_vessels()
        mlr.create_vessels_wl()
        mlr.create_mooring_licences()
        mlr.create_authuser_permits()
        mlr.create_waiting_list()
        mlr.create_dcv()

    OR
        from mooringlicensing.utils.mooring_licence_migrate_pd import MooringLicenceReader
        mlr=MooringLicenceReader('PersonDets.txt', 'MooringDets.txt', 'VesselDets.txt', 'UserDets.txt', 'ApplicationDets.txt', 'annual_admissions_booking_report.csv')
        mlr.run_migration

    FROM mgt-command:
        python manage_ds.py mooring_migration_script --filename mooringlicensing/utils/csv/MooringDets20221201-083202.txt


    Check for unique permit numbers
        pm = df['permit_number'].value_counts().loc[lambda x: x>1][1:].astype('int')
        pm.to_excel('/tmp/permit_numbers.xlsx')

    From RKS:

    from mooringlicensing.utils.mooring_licence_migrate_pd import MooringLicenceReader
    mlr=MooringLicenceReader('PersonDets.txt','MooringDets.txt','VesselDets.txt','UserDets.txt','ApplicationDets.txt','annual_admissions_booking_report.csv',path='/app/shared/clean/clean_22Dec2022/')


    CLEAR DB Records:
        MooringLicenceApplication.objects.all().delete()
        AuthorisedUserApplication.objects.all().delete()
        WaitingListApplication.objects.all().delete()
        DcvPermit.objects.all().delete()
        AnnualAdmissionApplication.objects.all().delete()
        ------

        Approval.objects.all().delete()
        Proposal.objects.all().delete()
        Vessel.objects.all().delete()
        Owner.objects.all().delete()
        Mooring.objects.all().delete()
        MooringBay.objects.all().delete()

        !./manage_ml.py loaddata mooringlicensing/fixtures/mooring_mooring_bay.json

    """

    def __init__(self, fname_user, fname_ml, fname_ves, fname_authuser, fname_wl, fname_aa, path='mooringlicensing/utils/csv/clean/'):
        self.df_user=pd.read_csv(path+fname_user, delimiter='|', dtype=str, low_memory=False)
        self.df_ml=pd.read_csv(path+fname_ml, delimiter='|', dtype=str)
        self.df_ves=pd.read_csv(path+fname_ves, delimiter='|', dtype=str)
        self.df_authuser=pd.read_csv(path+fname_authuser, delimiter='|', dtype=str)
        self.df_wl=pd.read_csv(path+fname_wl, delimiter='|', dtype=str)
        self.df_aa=pd.read_csv(path+fname_aa, delimiter='|', dtype=str)

        self.df_user=self._read_users()
        self.df_ml=self._read_ml()
        self.df_ves=self._read_ves()
        self.df_authuser=self._read_au()
        self.df_wl=self._read_wl()
        self.df_aa=self._read_aa()
        self.df_dcv=self._read_dcv()

        # create_users
        self.pers_ids = []
        self.user_created = []
        self.user_existing = []
        self.user_errors = []
        self.no_email = []

        # create_vessels
        self.vessels_created = []
        self.vessels_existing = []
        self.vessels_errors = []
        self.pct_interest_errors = []
        self.pct_interest_errors = []
        self.no_ves_rows = []

        # create_vessels
        self.ml_user_not_found = []
        self.ml_no_ves_rows = []

    def __get_phone_number(self, row):
        if 'work_number' in row and row.work_number:
            row.work_number.replace(' ', '')
            return row.work_number

        elif 'home_number' in row and row.home_number:
            row.home_number.replace(' ', '')
            return row.home_number

    def __get_work_number(self, row):
        return row.work_number.replace(' ', '')


    def __get_mobile_number(self, row):
        return row.mobile_number.replace(' ', '')

    def _read_users(self):
        # def _get_country_code(x):
        #     try:
        #         country=Country.objects.get(iso_3166_1_a2=x.get('country'))
        #     except Exception as e:
        #         country=Country.objects.get(iso_3166_1_a2='AU')
        #     return country.code


        # Rename the cols from Spreadsheet headers to Model fields names
        df_user = self.df_user.rename(columns=USER_COLUMN_MAPPING)
        df_user[df_user.columns] = df_user.apply(lambda x: x.str.strip() if isinstance(x, str) else x)
 
        # clean everything else
        df_user.fillna('', inplace=True)
        df_user.replace({np.NaN: ''}, inplace=True)

        #return df[:500]
        return df_user

    def _read_ml(self):

        # Rename the cols from Spreadsheet headers to Model fields names
        df_ml = self.df_ml.rename(columns=ML_COLUMN_MAPPING)
        df_ml[df_ml.columns] = df_ml.apply(lambda x: x.str.strip() if isinstance(x, str) else x)
 
        # clean everything else
        df_ml.fillna('', inplace=True)
        df_ml.replace({np.NaN: ''}, inplace=True)

        # filter cancelled and rows with no name
        df_ml = df_ml[(df_ml['cancelled']=='N') & (df_ml['first_name_l'].isna()==False)]

        #return df[:500]
        return df_ml

    def _read_ves(self):

        # clean everything else
        self.df_ves.fillna('', inplace=True)
        self.df_ves.replace({np.NaN: ''}, inplace=True)

        # filter cancelled and rows with no name
        #df_ml = df_ml[(df_ml['cancelled']=='N') & (df_ml['first_name_l'].isna()==False)]

        #return df[:500]
        return self.df_ves

    def _read_au(self):
        """ Read Auth User file """

        # Rename the cols from Spreadsheet headers to Model fields names
        df_authuser = self.df_authuser.rename(columns=AU_COLUMN_MAPPING)
        df_authuser[df_authuser.columns] = df_authuser.apply(lambda x: x.str.strip() if isinstance(x, str) else x)
 
        # clean everything else
        df_authuser.fillna('', inplace=True)
        df_authuser.replace({np.NaN: ''}, inplace=True)

        # filter cancelled and rows with no name
        df_authuser = df_authuser[(df_authuser['cancelled']=='N')]

        #return df[:500]
        return df_authuser

    def _read_wl(self):
        """ Read Auth User file """

        # Rename the cols from Spreadsheet headers to Model fields names
        df_wl = self.df_wl.rename(columns=WL_COLUMN_MAPPING)
        df_wl[df_wl.columns] = df_wl.apply(lambda x: x.str.strip() if isinstance(x, str) else x)
        df_wl['bay_pos_no']= pd.to_numeric(df_wl['bay_pos_no'])
 
        # clean everything else
        df_wl.fillna('', inplace=True)
        df_wl.replace({np.NaN: ''}, inplace=True)

        # filter cancelled and rows with no name
        df_wl = df_wl[(df_wl['app_status']=='W')]
        df_wl = df_wl.sort_values(['bay_pos_no'],ascending=True).groupby('bay').head(1000)

        #return df[:500]
        return df_wl

    def _read_dcv(self):
        """ Read PersonDets file - for DCV Permits details """
        if len(self.df_user)==0:
            _read_user()

        df_dcv = self.df_user[(self.df_user['company']!='') & (self.df_user['paid_up']=='Y') & (self.df_user['licences_type']=='C')]
        return df_dcv

    def _read_aa(self):
        """ Read Annual Admissions file created from 
            https://mooring-ria-internal.dbca.wa.gov.au/dashboard/bookings/annual-admissions/

            Eg. mooringlicensing/utils/csv/clean/annual_admissions_booking_report_20230125084027.csv
        """
        # Rename the cols from Spreadsheet headers to Model fields names
        df_aa = self.df_aa.rename(columns=AA_COLUMN_MAPPING)
        df_aa[df_aa.columns] = df_aa.apply(lambda x: x.str.strip() if isinstance(x, str) else x)
 
        # clean everything else
        df_aa.fillna('', inplace=True)
        df_aa.replace({np.NaN: ''}, inplace=True)

        # create dict of vessel details by rego_no --> self.vessels_dict['DO904']
        logger.info('Creating Vessels details Dictionary ...')
        self.create_vessels_dict()

        #df_dcv = self.df_user[(self.df_user['company']!='') & (self.df_user['paid_up']=='Y') & (self.df_user['licences_type']=='C')]
        return df_aa

    def run_migration(self):
        self.create_users()
        self.create_vessels()
        self.create_mooring_licences()
        self.create_authuser_permits()
        self.create_waiting_list()
        self.create_dcv()
        self.create_annual_admissions()

    def _run_migration(self):

        # create the users and organisations, if they don't already exist
        t0_start = time.time()
        self.create_users()
        self.create_organisations()
        t0_end = time.time()
        print('TIME TAKEN (Orgs and Users): {}'.format(t0_end - t0_start))

        # create the Migratiom models
        t1_start = time.time()
        self.create_licences()
        t1_end = time.time()
        print('TIME TAKEN (Create License Models): {}'.format(t1_end - t1_start))

        # create the Licence/Permit PDFs
        t2_start = time.time()
        try:
            self.create_licence_pdf()
        except Exception as e:
            print(e)
            import ipdb; ipdb.set_trace()
        t2_end = time.time()
        print('TIME TAKEN (Create License PDFs): {}'.format(t2_end - t2_start))

        print('TIME TAKEN (Total): {}'.format(t2_end - t0_start))

    def create_proposal_applicant(self, proposal, user, user_row):
        postal_same_as_res = user_row.address==user_row.postal_address or user_row.postal_address==''

        proposal_applicant = ProposalApplicant.objects.create(
           proposal=proposal,
           first_name=user_row.first_name,
           last_name=user_row.last_name,
           dob=parse(user_row.dob).date() if user_row.dob else None,

           residential_line1=user_row.address,
           residential_locality=user_row.suburb,
           residential_state=user_row.state,
           residential_postcode=user_row.postcode,

           postal_same_as_residential=postal_same_as_res,
           postal_line1=user_row.postal_address if not postal_same_as_res else '',
           postal_locality=user_row.postal_suburb,
           postal_state=user_row.postal_state if not postal_same_as_res else '',
           postal_postcode=user_row.postal_postcode if not postal_same_as_res else '',

           phone_number=self.__get_phone_number(user_row),
           mobile_number=self.__get_mobile_number(user_row),

           email=user.email,
        )

        #if user.email in self.user_created:
        user_dict = self.create_proposal_applicant_dict(user, proposal_applicant)
        if len(user_dict) > 0:
            # update address, phone details
            url = f'{settings.LEDGER_API_URL}/ledgergw/remote/update-userid/{user.id}/{settings.LEDGER_API_KEY}/'
            res = requests.post(url, data=user_dict)

    def create_proposal_applicant_aa(self, proposal, user, user_row):
        postal_same_as_res = True

        proposal_applicant = ProposalApplicant.objects.create(
           proposal=proposal,
           first_name=user_row.first_name,
           last_name=user_row.last_name,
           dob=parse(user_row.dob).date() if user_row.dob else None,

           residential_line1=user_row.address,
           residential_locality=user_row.suburb,
           residential_state=user_row.state,
           residential_postcode=user_row.postcode,

           postal_same_as_residential=postal_same_as_res,

           phone_number=self.__get_phone_number(user_row),
           mobile_number=self.__get_mobile_number(user_row),

           email=user.email,
        )

        #if user.email in self.user_created:
        user_dict = self.create_proposal_applicant_dict(user, proposal_applicant)
        if len(user_dict) > 0:
            # update address, phone details
            url = f'{settings.LEDGER_API_URL}/ledgergw/remote/update-userid/{user.id}/{settings.LEDGER_API_KEY}/'
            res = requests.post(url, data=user_dict)

    def create_proposal_applicant_dict(self, user, applicant):
        ''' user is ProposalApplicant '''

        payload = {}
        if not user.postal_address:
            postal_address = {
                "postal_line1": applicant.postal_line1,
                "postal_locality": applicant.postal_locality,
                "postal_state": applicant.postal_state,
                "postal_postcode": applicant.postal_postcode,
                "postal_same_as_residential": applicant.postal_same_as_residential,
                "postal_country": 'AU',
            }
            payload['postal_address'] = json.dumps(postal_address)

            if not user.postal_same_as_residential:
                residential_address = {
                    "residential_line1": applicant.residential_line1,
                    "residential_locality": applicant.residential_locality,
                    "residential_state": applicant.residential_state,
                    "residential_postcode": applicant.residential_postcode,
                    "residential_country": 'AU',
                }
                payload['residential_address'] = json.dumps(residential_address)


        if not user.dob:
            payload['dob'] = applicant.dob.strftime('%d/%m/%Y')
            
        if not user.phone_number:
            payload['phone_number'] = applicant.phone_number
            
        if not user.mobile_number:
            payload['mobile_number'] = applicant.mobile_number
            
        return payload

    def create_users(self):
        logger.info('Creating DCV users ...')
        df = self.df_dcv.groupby('pers_no').first()
        self._create_users_df(df)
        self.pers_ids_dcv = self.pers_ids

        logger.info('Creating ML & AU users ...')
        for pers_type in ['pers_no_u', 'pers_no_l']:
            df = self.df_authuser.groupby(pers_type).first()
            self._create_users_df(df)

        logger.info('Creating WL users ...')
        df = self.df_wl.groupby('pers_no').first()
        self._create_users_df(df)

        logger.info('Creating AA users ...')
        self._create_users_df_aa(self.df_aa)


    def _create_users_df(self, df):
        # Iterate through the dataframe and create non-existent users
        for index, row in tqdm(df.iterrows(), total=df.shape[0]):
            #if row.status != 'Vacant':
            try:
#                if row.name == '206441':
#                    import ipdb; ipdb.set_trace()
#                else:
#                    continue

                if not row.name :
                    continue

                user_row = self.df_user[self.df_user['pers_no']==row.name] #.squeeze() # as Pandas Series
                if user_row.empty:
                    continue

                if len(user_row)>1:
                    user_row = user_row[user_row['paid_up']=='Y']
                    if user_row.empty:
                        continue
                    if len(user_row)>1:
                        # if still greater than 1, take first
                        user_row = user_row[:1]

                user_row = user_row.squeeze() # convert to Pandas Series

                email = user_row.email.lower().replace(' ','')
                if not email:
                    self.no_email.append(user_row.pers_no)
                    continue

                first_name = user_row.first_name.lower().title().strip()
                last_name = user_row.last_name.lower().title().strip()

                resp = get_or_create(email)                    
                if resp['status'] == 200:
                    if resp['data']['record_status'] == 'new' and email not in self.user_existing:
                        self.user_created.append(email)
                    elif resp['data']['record_status'] == 'existing' and email not in self.user_existing:
                        self.user_existing.append(email)
                else:
                    logger.error(f'User creation failed: {email}')
                    self.user_errors.append(user_row.email)

                user_id = resp['data']['emailuser_id']
                self.pers_ids.append((user_id, row.name))

            except Exception as e:
                import ipdb; ipdb.set_trace()
                self.user_errors.append(user_row.email)
                logger.error(f'user: {row.name}   *********** 1 *********** FAILED. {e}')

        print(f'users created:  {len(self.user_created)}')
        print(f'users existing: {len(self.user_existing)}')
        print(f'users errors:   {len(self.user_errors)}')
        print(f'no_email errors:   {len(self.no_email)}')
        print(f'users errors:   {self.user_errors}')
        print(f'no_email errors:   {self.no_email}')

    def _create_users_df_aa(self, df):
        """ Reads the annual_admissions file created from the Mooring Booking System """
        # Iterate through the dataframe and create non-existent users
        for index, row in tqdm(df.iterrows(), total=df.shape[0]):
            #if row.status != 'Vacant':
            try:
                #if row.name == '206846':
                #    import ipdb; ipdb.set_trace()

                #user_row = self.df_user[self.df_user['pers_no']==row.name] #.squeeze() # as Pandas Series
                user_row = row.copy()

                email = user_row.email.lower().replace(' ','')
                if not email:
                    self.no_email.append(user_row.pers_no)
                    continue

                #if email == 'rod@wmpg.com.au':
                #    import ipdb; ipdb.set_trace()

                first_name = user_row.first_name.lower().title().strip()
                last_name = user_row.last_name.lower().title().strip()

                resp = get_or_create(email)                    
                if resp['status'] == 200:
                    if resp['data']['record_status'] == 'new' and email not in self.user_existing:
                        self.user_created.append(email)
                    elif resp['data']['record_status'] == 'existing' and email not in self.user_existing:
                        self.user_existing.append(email)
                else:
                    logger.error(f'User creation failed: {email}')
                    self.user_errors.append(user_row.email)

                user_id = resp['data']['emailuser_id']
                self.pers_ids.append((user_id, row.name))

#                self.pers_ids.append((user.id, row.name))
#                users = EmailUser.objects.filter(email=email)
#                if users.count() == 0:
#                    user = EmailUser.objects.create(
#                        email=email,
#                        first_name=first_name,
#                        last_name=last_name,
#                        #phone_number=self.__get_work_number(user_row),
#                        phone_number=self.__get_phone_number(user_row),
#                        mobile_number=self.__get_mobile_number(user_row)
#                    )
#
#                    country = Country.objects.get(printable_name__icontains='AUSTRALIA')
# 
#                    self.user_created.append(email)
#                else:
#                    user = users[0]
#                    # update user details
#                    user.first_name = first_name
#                    user.last_name = last_name
#                    #user.phone_number = self.__get_work_number(user_row)
#                    user.phone_number = self.__get_phone_number(user_row),
#                    user.mobile_number = self.__get_mobile_number(user_row)
#
#                    country = Country.objects.get(printable_name__icontains='AUSTRALIA')
#              
#                self.pers_ids.append((user.id, row.name))


            except Exception as e:
                import ipdb; ipdb.set_trace()
                self.user_errors.append(user_row.email)
                logger.error(f'user: {row.name}   *********** 1 *********** FAILED. {e}')

        print(f'users created:  {len(self.user_created)}')
        print(f'users existing: {len(self.user_existing)}')
        print(f'users errors:   {len(self.user_errors)}')
        print(f'no_email errors:   {len(self.no_email)}')
        print(f'users errors:   {self.user_errors}')
        print(f'no_email errors:   {self.no_email}')

    def create_vessels(self):
        self._create_vessels()
        self._create_vessels_wl()

    def _create_vessels(self):

        def _ves_fields(prefix, ves_row, pers_no):
            ''' 
            Returns the 7 fields for a given field type (ves_dets file allows upto 7 vessels per pers_no/owner)
                Eg.
                    ves_fields('DoT Rego', ves_row, '000477')
                    Out[201]: ['GC1', 'DW106', 'DK819', 'EZ315', 'EF82']
            '''
            field1 = prefix + ' Nominated Ves'
            field2 = prefix + ' Ad Ves 2'
            field3 = prefix + ' Ad Ves 3'
            field4 = prefix + ' Ad Ves 4'
            field5 = prefix + ' Ad Ves 5'
            field6 = prefix + ' Ad Ves 6'
            field7 = prefix + ' Ad Ves 7'

          
            ves_list_raw = ves_row[[field1, field2, field3, field4, field5, field6, field7]].values.flatten().tolist()
            #return [i.rstrip(pers_no) for i in ves_list_raw if i.rstrip(pers_no)]  
            return [i[:-len(pers_no)] for i in ves_list_raw if i!=pers_no]  

        def ves_fields(ves_row):
            ves_details = []
            for colname_postfix in ['Nominated Ves', 'Ad Ves 2', 'Ad Ves 3', 'Ad Ves 4', 'Ad Ves 5', 'Ad Ves 6', 'Ad Ves 7']:
                #cols = [col for col in ves_row.columns if (colname_postfix in col)]
                cols = [col for col in ves_row.index if (colname_postfix in col)]

                #ves_detail = ves_row[cols].to_dict(orient='records')[0]
                #ves_detail = ves_row[cols].to_dict(orient='records')
                ves_detail = ves_row[cols].to_dict()

                #if ves_row['H. I. N. ' + colname_postfix].iloc[0] != '' or ves_row['DoT Rego ' + colname_postfix].iloc[0] != '':
                if ves_row['H. I. N. ' + colname_postfix] != '' or ves_row['DoT Rego ' + colname_postfix] != '':
                    ves_details.append(ves_detail)
                #ves_details.append(ves_detail)

            return ves_details

        def try_except(value):
            #if pers_no=='213162':
            #    import ipdb; ipdb.set_trace()

            try:
                val = float(value)
            except Exception:
                val = 0.00
            return val

        self.vessels_au = {} 
        self.vessels_dcv = {} 
        postfix = ['Nominated Ves', 'Ad Ves 2', 'Ad Ves 3', 'Ad Ves 4', 'Ad Ves 5', 'Ad Ves 6', 'Ad Ves 7']
        for user_id, pers_no in tqdm(self.pers_ids):
            try:
                #if pers_no=='202600':
                #    import ipdb; ipdb.set_trace()

                ves_rows = self.df_ves[self.df_ves['Person No']==pers_no]
                if len(ves_rows) > 1:
                    ves_rows = ves_rows[(ves_rows['Au Sticker No Nominated Ves']!='')]
                    self.no_ves_rows.append((pers_no, len(ves_rows)))

                for idx, row in ves_rows.iterrows():
                    ves_row = row.to_frame()

                    ves_list = ves_fields(row)

                    try:
                        #owner = Owner.objects.get(emailuser_id=user_id)
                        owner = Owner.objects.get(emailuser=user_id)
                    except ObjectDoesNotExist:
                        #owner = Owner.objects.create(emailuser_id=user_id)
                        owner = Owner.objects.create(emailuser=user_id)

                    au_vessels = {}
                    c_vessels = []
                    for i, ves in enumerate(ves_list):
                        ves_name=ves['Name ' + postfix[i]]
                        ves_type=ves['Type ' + postfix[i]]
                        rego_no=ves['DoT Rego ' + postfix[i]]
                        pct_interest=ves['%Interest ' + postfix[i]]
                        tot_length=ves['Reg Length ' + postfix[i]]
                        draft=ves['Draft ' + postfix[i]]
                        tonnage=ves['Tonnage ' + postfix[i]]
                        au_sticker=ves['Au Sticker No ' + postfix[i]]
                        c_sticker=ves['C Sticker No ' + postfix[i]]
                        au_sticker_date=ves['Date Au Sticker Sent ' + postfix[i]]

#                        if rego_no in ['DY167', '81900']:
#                            import ipdb; ipdb.set_trace()
#                        else:
#                            continue

                        try:
                            ves_type = VESSEL_TYPE_MAPPING[ves_type]
                        except Exception as e:
                            ves_type = 'other'

                        try:
                            vessel = Vessel.objects.get(rego_no=rego_no)
                        except ObjectDoesNotExist:
                            vessel = Vessel.objects.create(rego_no=rego_no)

                        try:
                            vessel_ownership = VesselOwnership.objects.get(owner=owner, vessel=vessel)
                        except ObjectDoesNotExist:
                            pct_interest = int(round(float(try_except(pct_interest)),0))
                            if pct_interest < 25:
                                self.pct_interest_errors.append((pers_no, rego_no, pct_interest))
                                pct_interest = 100
                            vessel_ownership = VesselOwnership.objects.create(owner=owner, vessel=vessel, percentage=pct_interest)

                        try:
                            vessel_details = VesselDetails.objects.get(vessel=vessel)
                            vessel_details.vessel_type = ves_type
                            vessel_details.save()
                        except MultipleObjectsReturned:
                            vessel_details = VesselDetails.objects.filter(vessel=vessel)[0]
                            vessel_details.vessel_type = ves_type
                            vessel_details.save()
                        except:
                            vessel_details = VesselDetails.objects.create(
                                vessel=vessel,
                                vessel_type=ves_type,
                                vessel_name=ves_name,
                                vessel_length=try_except(tot_length),
                                vessel_draft=try_except(draft),
                                vessel_weight=try_except(tonnage),
                                berth_mooring=''
                            )
                        #au_vessels.update({rego_no:au_sticker, sticker_sent:})
                        self.vessels_au.update({rego_no: dict(au_sticker=au_sticker, au_sticker_sent=au_sticker_date)})
                        c_vessels.append((rego_no, c_sticker))
                        #if pers_no=='200700':
                        #    import ipdb; ipdb.set_trace()

                    #self.vessels_au.update({pers_no:au_vessels})
                    self.vessels_dcv.update({pers_no:c_vessels})
                    self.vessels_created.append((pers_no, len(c_vessels), c_vessels))

            except Exception as e:
                self.vessels_errors.append((pers_no, str(e)))
                continue

        print(f'vessels created:  {len(self.vessels_created)}')
        print(f'vessels errors:   {self.vessels_errors}')
        print(f'vessels errors:   {len(self.vessels_errors)}')
        print(f'vessels pct_int errors:   {self.pct_interest_errors}')
        print(f'vessels pct_int errors:   {len(self.pct_interest_errors)}')
        print(f'vessels no_ves_rows: {self.no_ves_rows}: Total len({self.no_ves_rows})')

    def _create_vessels_wl(self):

        def is_invalid_rego(element: any) -> bool:
            #If you expect None to be passed:
            if element is None:
                return True
            try:
                v = int(element)
                return v==0
            except ValueError:
                return False

        def try_except(value):
            #if pers_no=='213162':
            #    import ipdb; ipdb.set_trace()

            try:
                val = float(value)
            except Exception:
                val = 0.00
            return val

        self.dummy_vessels = []
        #for user_id, pers_no in tqdm(self.pers_ids):
        #df_wl = self.df_wl[(self.df_wl['vessel_rego']!='0')].groupby('vessel_rego').first()
        df_wl = self.df_wl.groupby('vessel_rego').first()
        for index, row in tqdm(df_wl.iterrows(), total=df_wl.shape[0]):
            try:
#                if row.pers_no=='088452':
#                    import ipdb; ipdb.set_trace()
#                else:
#                    continue

                rego_no = row.name
                if is_invalid_rego(rego_no):
                    # User is on waiting list without a vessel - assign a dummy vessel
                    rego_no = f'{row.first_name}_{row.last_name}_{row.pers_no}'.lower()
                    self.dummy_vessels.append(rego_no)

                vessel, created = Vessel.objects.get_or_create(rego_no=rego_no)

                user_row = self.df_user[self.df_user['pers_no']==row.pers_no] #.squeeze() # as Pandas Series
                if user_row.empty:
                    continue

                if len(user_row)>1:
                    # if still greater than 1, take first
                    user_row = user_row[:1]
                user_row = user_row.squeeze() # convert to Pandas Series

                #if not user_row.empty and user_row.pers_no=='000036':
                #    import ipdb; ipdb.set_trace()

                email = user_row.email.lower().replace(' ','')
                if not email:
                    self.no_email.append(user_row.pers_no)
                    continue

#                if email != 'ftroop351@gmail.com':
#                    #import ipdb; ipdb.set_trace()
#                    continue

                users = EmailUser.objects.filter(email=email)
                if users.count() == 0:
                    print(f'wl - email not found: {email}')
 
                    self.ml_user_not_found.append(email)
                    continue
                else:
                    user = users[0]
                    self.user_existing.append(email)


                try:
                    #owner = Owner.objects.get(emailuser_id=user_id)
                    owner = Owner.objects.get(emailuser=user.id)
                except ObjectDoesNotExist:
                    #owner = Owner.objects.create(emailuser_id=user_id)
                    owner = Owner.objects.create(emailuser=user.id)

                ves_type = 'other'
                vessel_ownership = VesselOwnership.objects.create(owner=owner, vessel=vessel, percentage=100)
                if rego_no in self.dummy_vessels:
                    vessel_ownership.end_date = TODAY

                try:
                    vessel_details = VesselDetails.objects.get(vessel=vessel)
                    vessel_details.vessel_type = ves_type
                    vessel_details.save()
                except MultipleObjectsReturned:
                    vessel_details = VesselDetails.objects.filter(vessel=vessel)[0]
                    vessel_details.vessel_type = ves_type
                    vessel_details.save()
                except:
                    vessel_details = VesselDetails.objects.create(
                        vessel=vessel,
                        vessel_type=ves_type,
                        vessel_name=row.vessel_name,
                        vessel_length=try_except(row.vessel_length),
                        vessel_draft=try_except(row.vessel_draft),
                        vessel_weight=try_except(0.0),
                        berth_mooring=''
                    )

                #self.vessels_created.append((row.pers_no, 1, c_vessels))

            except Exception as e:
                self.vessels_errors.append((row.pers_no, str(e)))
                continue

        print(f'vessels created:  {len(self.vessels_created)}')
        print(f'vessels errors:   {self.vessels_errors}')
        print(f'vessels errors:   {len(self.vessels_errors)}')


    def create_vessels_dict(self):

        def ves_fields(ves_row):
            ves_details = []
            for colname_postfix in ['Nominated Ves', 'Ad Ves 2', 'Ad Ves 3', 'Ad Ves 4', 'Ad Ves 5', 'Ad Ves 6', 'Ad Ves 7']:
                cols = [col for col in ves_row.index if (colname_postfix in col)]

                ves_detail = ves_row[cols].to_dict()

                if ves_row['H. I. N. ' + colname_postfix] != '' or ves_row['DoT Rego ' + colname_postfix] != '':
                    ves_details.append(ves_detail)

            return ves_details

        self.vessels_dict = {} 
        postfix = ['Nominated Ves', 'Ad Ves 2', 'Ad Ves 3', 'Ad Ves 4', 'Ad Ves 5', 'Ad Ves 6', 'Ad Ves 7']
        for idx, row in tqdm(self.df_ves.iterrows(), total=self.df_ves.shape[0]):
            ves_row = row.to_frame()

            ves_list = ves_fields(row)

            au_vessels = {}
            c_vessels = []
            for i, ves in enumerate(ves_list):
                try:
                    ves_name=ves['Name ' + postfix[i]]
                    ves_type=ves['Type ' + postfix[i]]
                    rego_no=ves['DoT Rego ' + postfix[i]]
                    pct_interest=ves['%Interest ' + postfix[i]]
                    tot_length=ves['Reg Length ' + postfix[i]]
                    draft=ves['Draft ' + postfix[i]]
                    tonnage=ves['Tonnage ' + postfix[i]]
                    beam=ves['Beam ' + postfix[i]]
                    ml_sticker=ves['Lic Sticker Number ' + postfix[i]]
                    au_sticker=ves['Au Sticker No ' + postfix[i]]
                    c_sticker=ves['C Sticker No ' + postfix[i]]
                    au_sticker_date=ves['Date Au Sticker Sent ' + postfix[i]]

                    #if rego_no=='DO904':
                    #    import ipdb; ipdb.set_trace()

                    self.vessels_dict.update({rego_no:
                        {
                            'length':        tot_length,
                            'draft':         draft,
                            'beam':          beam,
                            'weight':        tonnage,
                            'name':          ves_name,
                            'type':          ves_type,
                            'percentage':    pct_interest,
                            'ml_sticker':    ml_sticker,
                            'au_sticker':    au_sticker,
                            'c_sticker':     c_sticker,
                            'au_sticker_date': au_sticker_date,
                        }
                    })

                except Exception as e:
                    print(f'ERROR: vessels_dict: {str(e)}')
                    if e.args[0]=='Name Ad Ves 2':
                        continue
                    else:
                        import ipdb; ipdb.set_trace()
                        pass
                    


        print(f'vessels_dict dict size:  {len(self.vessels_dict)}')

    def create_mooring_licences(self):
        expiry_date = EXPIRY_DATE
        start_date = START_DATE
        date_applied = DATE_APPLIED
        mooring_not_found = []
        vessel_not_found = []
        owner_not_found = []
        vessel_ownership_not_found = []
        errors = []

        # Iterate through the ml dataframe and create MooringLicenceApplication's
        df = self.df_ml.groupby('mooring_no').first()
        for index, row in tqdm(df.iterrows(), total=df.shape[0]):
            try:
                
#                if row.pers_no == '206441':
#                    import ipdb; ipdb.set_trace()
#                else:
#                    continue

                if not row.name or len([_str for _str in ['KINGSTON REEF','NN64','PB 02','RIA'] if row.name in _str])>0:
                    continue

                user_row = self.df_user[self.df_user['pers_no']==row.pers_no] #.squeeze() # as Pandas Series

                if user_row.empty:
                    continue

                if len(user_row)>1:
                    #user_row = user_row[(user_row['paid_up']=='Y') | (user_row['current_mooring_no']!='')]
                    user_row = user_row[(user_row['paid_up']=='Y')]

                if user_row.empty:
                    continue
                if len(user_row)>1:
                    # if still greater than 1, take first
                    user_row = user_row[:1]
                user_row = user_row.squeeze() # convert to Pandas Series

                #if not user_row.empty and user_row.pers_no=='000036':
                #    import ipdb; ipdb.set_trace()

                email = user_row.email.lower().replace(' ','')
                if not email:
                    self.no_email.append(user_row.pers_no)
                    continue

#                if email != 'ftroop351@gmail.com':
#                    #import ipdb; ipdb.set_trace()
#                    continue

                users = EmailUser.objects.filter(email=email)
                if users.count() == 0:
                    print(f'ml - email not found: {email}')
 
                    self.ml_user_not_found.append(email)
                    continue
                else:
                    user = users[0]
                    self.user_existing.append(email)


                ves_row = self.df_ves[self.df_ves['Person No']==row.pers_no]
                if len(ves_row) > 1:
                    self.ml_no_ves_rows.append((user_row.pers_no, len(ves_row)))
                    ves_row = ves_row.iloc[0]
                else:
                    ves_row = ves_row.squeeze()
#
#                ves_list = ves_fields(ves_row)


 
                berth_mooring = ''
                #vessel_data.append([pers_no, email, user.dob, rego_no, dot_name, percentage, vessel_type, vessel_name, vessel_overall_length, vessel_draft, vessel_weight, berth_mooring])

                postfix = 'Nominated Ves'
                ves_name = ves_row['Name ' + postfix]
                ves_type = ves_row['Type ' + postfix]
                rego_no = ves_row['DoT Rego ' + postfix]
                pct_interest = ves_row['%Interest ' + postfix]
                tot_length = ves_row['Reg Length ' + postfix]
                draft = ves_row['Draft ' + postfix]
                tonnage = ves_row['Tonnage ' + postfix]
                sticker_number = ves_row['Lic Sticker Number ' + postfix] #if ves_row['Lic Sticker Number ' + postfix] else None
                sticker_sent = ves_row['Licencee Sticker Sent ' + postfix] #if ves_row['Licencee Sticker Sent ' + postfix] else None
 
                ves_type = VESSEL_TYPE_MAPPING.get(ves_type, 'other')
#                sticker_numbers    = ves_fields('Lic Sticker Number', ves_row, pers_no)
#                sticker_sent       = ves_fields('Licencee Sticker Sent', ves_row, pers_no)

                if ves_name=='':
                    continue 

                #if not user_row.empty and user_row.pers_no=='000036':
                #    import ipdb; ipdb.set_trace()

                try:
                    owner = Owner.objects.get(emailuser=user.id)
                except Exception as e:
                    owner_not_found.append((row.pers_no, user, rego_no))
                    continue

                try:
                    vessel = Vessel.objects.get(rego_no=rego_no)
                except Exception as e:
                    vessel_not_found.append(rego_no)
                    continue

                try:
                    vessel_ownership = VesselOwnership.objects.get(owner=owner, vessel=vessel)
                except Exception as e:
                    vessel_ownership_not_found.append(rego_no)
                    continue
                try:
                    vessel_details = VesselDetails.objects.get(vessel=vessel)
                except MultipleObjectsReturned:
                    vessel_details = VesselDetails.objects.filter(vessel=vessel)[0]
                vessel_details.vessel_type = ves_type
                vessel_details.save()

                if Mooring.objects.filter(name=row.name).count()>0:
                    mooring = Mooring.objects.filter(name=row.name)[0]
                else:
                    #print(f'Mooring not found: {row.name}')
                    #errors.append(dict(idx=index, record=ves_row))
                    mooring_not_found.append(row.name)
                    continue


#                mla=MooringLicenceApplication.objects.filter(
#                    vessel_details=vessel_details,
#                    vessel_ownership=vessel_ownership,
#                    rego_no=rego_no,
#                ).exclude(current_proposal=Proposal.PROCESSING_STATUS_DECLINED)
#                if mla.count()>0:
#                    # already exists
#                    continue

                proposal=MooringLicenceApplication.objects.create(
                    #proposal_type_id=1, # new application
                    proposal_type_id=ProposalType.objects.get(code='new').id, # new application
                    submitter=user.id,
                    lodgement_date=datetime.datetime.now().astimezone(),
                    migrated=True,
                    vessel_details=vessel_details,
                    vessel_ownership=vessel_ownership,
                    rego_no=rego_no,
                    vessel_type=ves_type,
                    vessel_name=ves_name,
                    vessel_length=vessel_details.vessel_length,
                    vessel_draft=vessel_details.vessel_draft,
                    vessel_beam=vessel_details.vessel_beam,
                    vessel_weight=vessel_details.vessel_weight,
                    berth_mooring=vessel_details.berth_mooring,
                    preferred_bay= mooring.mooring_bay,
                    percentage=vessel_ownership.percentage,
                    individual_owner=True,
                    #proposed_issuance_approval={},
                    processing_status='approved',
                    customer_status='approved',
                    allocated_mooring=mooring,
                    proposed_issuance_approval={
                        "start_date": start_date.strftime('%d/%m/%Y'),
                        "expiry_date": expiry_date.strftime('%d/%m/%Y'),
                        "details": None,
                        "cc_email": None,
                        "mooring_id": mooring.id,
                        "ria_mooring_name": mooring.name,
                        "mooring_bay_id": mooring.mooring_bay.id,
                        "vessel_ownership": [{
                                "id": vessel_ownership.id,
                                "dot_name": rego_no,
                                "checked": True
                            }],
                        "mooring_on_approval": []
                    },
                    date_invited=None, #date_invited,
                    dot_name=rego_no,
                )

                self.create_proposal_applicant(proposal, user, user_row)

                #if not user_row.empty and user_row.pers_no=='000036':
                #    import ipdb; ipdb.set_trace()

                date_invited = ''

                pua=ProposalUserAction.objects.create(
                    proposal=proposal,
                    who=user.id,
                    what='Mooring Site Licence - Migrated Application',
                )


                approval = MooringLicence.objects.create(
                    status='current',
                    #internal_status=None,
                    current_proposal=proposal,
                    issue_date = datetime.datetime.now(datetime.timezone.utc),
                    #start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').date(),
                    start_date = start_date,
                    expiry_date = expiry_date,
                    submitter=user.id,
                    migrated=True,
                    export_to_mooring_booking=True,
                )

                aua=ApprovalUserAction.objects.create(

                    approval=approval,
                    who=user.id,
                    what='Mooring Site Licence - Migrated Application',
                )

                proposal.approval = approval
                proposal.save()
                alloc_mooring = proposal.allocated_mooring
                alloc_mooring.mooring_licence = approval
                alloc_mooring.save()

                vooa = VesselOwnershipOnApproval.objects.create(
                    approval=approval,
                    vessel_ownership=vessel_ownership,
                )

                moa = MooringOnApproval.objects.create(
                    approval=approval,
                    mooring=mooring,
                    sticker=None,
                    site_licensee=True, # ???
                    end_date=expiry_date
                )

                try:
                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').astimezone(datetime.timezone.utc)
                except:
                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d').astimezone(datetime.timezone.utc)

                approval_history = ApprovalHistory.objects.create(
                    reason='new',
                    approval=approval,
                    vessel_ownership = vessel_ownership,
                    proposal = proposal,
                    #start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').astimezone(datetime.timezone.utc)
                    start_date = start_date,
                )

                #if row.pers_no == '000036':
                #    import ipdb; ipdb.set_trace()

                try:
                    mailing_date = datetime.datetime.strptime(sticker_sent, '%d/%m/%Y').date() if sticker_sent else None
                except:
                    mailing_date = None


                if sticker_number:
                    sticker = Sticker.objects.create(
                        number=sticker_number,
                        status=Sticker.STICKER_STATUS_CURRENT, # 'current'
                        approval=approval,
                        proposal_initiated=proposal,
                        vessel_ownership=vessel_ownership,
                        printing_date=None, #TODAY,
                        #mailing_date=sticker_sent, #TODAY,
                        #mailing_date=datetime.datetime.strptime(sticker_sent, '%d/%m/%Y').date() if sticker_sent else None,
                        mailing_date=mailing_date,
                        sticker_printing_batch=None,
                        sticker_printing_response=None,
                    )

                    approval_history.stickers.add(sticker.id)
                #approval.generate_doc()

            except Exception as e:
                logger.error(f'ERROR: {row.name}. {str(e)}')
                import ipdb; ipdb.set_trace()
                self.user_errors.append(user_row.email)

        print(f'ml_user_not_found:  {self.ml_user_not_found}')
        print(f'Duplicate Pers_No in ves_row:  {self.ml_no_ves_rows}')
        print(f'ml_user_not_found:  {len(self.ml_user_not_found)}')
        print(f'Duplicate Pers_No in ves_row:  {len(self.ml_no_ves_rows)}')
        print(f'Moorings not Found:  {mooring_not_found}')
        print(f'Moorings not Found:  {len(mooring_not_found)}')
        print(f'Owners not Found:  {owner_not_found}')
        print(f'VesselOwnership not Found:  {len(vessel_ownership_not_found)}')
        print(f'VesselOwnership not Found:  {vessel_ownership_not_found}')
        print(f'Owners not Found:  {len(owner_not_found)}')
        print(f'Vessels not Found:  {vessel_not_found}')
        print(f'Vessels not Found:  {len(vessel_not_found)}')

    def create_authuser_permits(self):
        expiry_date = EXPIRY_DATE
        start_date = START_DATE
        date_applied = DATE_APPLIED

        errors = []
        vessel_not_found = []
        aup_created = []
        au_stickers = []
        no_au_stickers = []

        bay_preferences_numbered = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
        vessel_type = 'other'

        #for index, row in tqdm(self.df_authuser.iterrows(), total=self.df_authuser.shape[0]):
        df_authuser = self.df_authuser[(self.df_authuser['vessel_rego']!='0')].groupby('vessel_rego').first()
        for index, row in tqdm(df_authuser.iterrows(), total=df_authuser.shape[0]):
            #if row.status != 'Vacant':
            try:
#                if row.pers_no_l != '206441':
#                    #import ipdb; ipdb.set_trace()
#                    continue

                #if row.pers_no_l == '019626':
                #    print(f"rego_no: {row['vessel_rego']}")
                #    import ipdb; ipdb.set_trace()
                rego_no = row.name
                    
                if row.mooring_no == 'TB999':
                    # exclude mooring
                    continue

                #if row.pers_no_u == '210758':
                #    import ipdb; ipdb.set_trace()

#                if rego_no=='GV331' and row.mooring_no=='PB008':
#                    import ipdb; ipdb.set_trace()
#                else:
#                    continue

               
                mooring_authorisation_preference = 'site_licensee' if row['licencee_approved']=='Y' else 'ria'
                mooring = Mooring.objects.filter(name=row['mooring_no'])[0]
                #licensee = EmailUser.objects.get(first_name=row['first_name_l'].lower().capitalize(), last_name=row['last_name_l'].lower().capitalize())
                #email_l = self.df_user[(self.df_user['pers_no']==row['pers_no_l'])].iloc[0]['email'].strip()
                email_l = self.df_user[(self.df_user['pers_no']==row['pers_no_l']) & (self.df_user['email']!='')].iloc[0]['email'].strip()
                try:
                    licensee = EmailUser.objects.get(email=email_l.lower())
                except Exception as e:
                    licensee = EmailUser.objects.get(first_name=row['first_name_l'].lower().capitalize(), last_name=row['last_name_l'].lower().capitalize()) 
                #user = EmailUser.objects.filter(first_name=row['first_name_u'].lower().capitalize(), last_name=row['last_name_u'].lower().capitalize())[0]
                if not row['first_name_u']:
                    # This record represents Mooring Licence Holder - No need for an Auth User Permit
                    continue

                email_u = self.df_user[(self.df_user['pers_no']==row.pers_no_u) & (self.df_user['email']!='')].iloc[0]['email'].strip()
                try:
                    user = EmailUser.objects.get(email=email_u.lower())
                except Exception as e:
                    user = EmailUser.objects.get(first_name=row['first_name_u'].lower().capitalize(), last_name=row['last_name_u'].lower().capitalize()) 
                #email_u = self.df_user[(self.df_user['first_name']=='Alisa') & (self.df_user['last_name']=='Simich')].iloc[0]['email']
                #email_u = self.df_user[(self.df_user['first_name']==row['first_name_u']) & (self.df_user['last_name']==row['last_name_u'])].iloc[0]['email']
                #user = EmailUser.objects.get(email=email_u)

                #sticker_number = row['sticker'],
                #rego_no = row['vessel_rego']
                rego_no = row.name
                #if sticker_number == 31779:
                #    import ipdb; ipdb.set_trace()
                sticker_info = self.vessels_au.get(rego_no)
                if sticker_info:
                    sticker_number = sticker_info['au_sticker']
                    sticker_sent = sticker_info['au_sticker_sent']

                if sticker_number is None:
                    no_au_stickers.append(rego_no)
                else:
                    au_stickers.append(rego_no)

                date_issued = row['date_issued'].split(' ')[0]
                try:
                    vessel = Vessel.objects.get(rego_no=rego_no)
                except Exception as e:
                    vessel_not_found.append(f'{row.pers_no_u} - {email_u}: {rego_no}')
                    continue

                vessel_ownership = vessel.vesselownership_set.all()[0]
                vessel_details = vessel.vesseldetails_set.all()[0]

#                if email_u == 31779:
#                auth_user = AuthorisedUserApplication.objects.filter(
#                    site_licensee_email=licensee.email,
#                    mooring=mooring,
#                    vessel_details=vessel_details,
#                    vessel_ownership=vessel_ownership,
#                    rego_no=rego_no,
#                )
#                if len(auth_user)>0:
#                    # already exists
#                    continue

#                if rego_no=='GV331' and row.mooring_no=='PB008':
#                    import ipdb; ipdb.set_trace()
#                else:
#                    continue


                proposal=AuthorisedUserApplication.objects.create(
                    #proposal_type_id=1, # new application
                    proposal_type_id=ProposalType.objects.get(code='new').id, # new application
                    submitter=user.id,
                    lodgement_date=TODAY, #datetime.datetime.now(),
                    mooring_authorisation_preference=mooring_authorisation_preference,
                    site_licensee_email=licensee.email,
                    keep_existing_mooring=True,
                    mooring=mooring,
                    bay_preferences_numbered=bay_preferences_numbered,
                    migrated=True,
                    vessel_details=vessel_details,
                    vessel_ownership=vessel_ownership,
                    rego_no=rego_no,
                    vessel_type=vessel_type,
                    vessel_name=row['vessel_name'],
                    #vessel_length=row['vessel_length'],
                    #vessel_draft=row['vessel_draft'],
                    vessel_length=vessel_details.vessel_length,
                    vessel_draft=vessel_details.vessel_draft,
                    vessel_beam=vessel_details.vessel_beam,
                    vessel_weight=vessel_details.vessel_weight,
                    berth_mooring=vessel_details.berth_mooring,
                    preferred_bay= mooring.mooring_bay,
                    percentage=vessel_ownership.percentage,
                    individual_owner=True,
                    dot_name=vessel_ownership.dot_name,
                    #proposed_issuance_approval={},
                    processing_status='approved',
                    customer_status='approved',
                    proposed_issuance_approval={
                        "details": None,
                        "cc_email": None,
                        "mooring_id": mooring.id,
                        "ria_mooring_name": mooring.name,
                        "mooring_bay_id": mooring.mooring_bay.id,
                        "vessel_ownership": [],
                        "mooring_on_approval": []
                    },
                )

                #import ipdb; ipdb.set_trace()
                try:
                    user_row = self.df_user[self.df_user['pers_no']==row.pers_no_u].squeeze() # as Pandas Series
                except Exception as e:
                    import ipdb; ipdb.set_trace()
                self.create_proposal_applicant(proposal, user, user_row)

                ua=ProposalUserAction.objects.create(
                    proposal=proposal,
                    who=user.id,
                    what='Authorised User Permit - Migrated Application',
                )

                try:
                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').date()
                except:
                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d').date()

                #approval = WaitingListAllocation.objects.create(
                approval = AuthorisedUserPermit.objects.create(
                    status='current',
                    #internal_status=None,
                    current_proposal=proposal,
                    issue_date = datetime.datetime.now(datetime.timezone.utc),
                    #start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').date(),
                    start_date = start_date,
                    expiry_date = expiry_date,
                    submitter=user.id,
                    migrated=True,
                    export_to_mooring_booking=True,
                )
                #print(f'wla_order: {position_no}')

                proposal.approval = approval
                proposal.save()
                aup_created.append(approval.id)

                aua=ApprovalUserAction.objects.create(
                    approval=approval,
                    who=user.id,
                    what='Authorised User Permit - Migrated Application',
                )

                try:
                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').astimezone(datetime.timezone.utc)
                except:
                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d').astimezone(datetime.timezone.utc)

#                approval_history = ApprovalHistory.objects.create(
#                    reason='new',
#                    approval=approval,
#                    vessel_ownership = vessel_ownership,
#                    proposal = proposal,
#                    #start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').astimezone(datetime.timezone.utc)
#                    start_date = start_date,
#                )

                sticker = None
                if sticker_number:
                    sticker = Sticker.objects.create(
                        number=sticker_number,
                        status=Sticker.STICKER_STATUS_CURRENT, # 'current'
                        approval=approval,
                        proposal_initiated=proposal,
                        vessel_ownership=vessel_ownership,
                        printing_date=None, #TODAY,
                        #mailing_date=sticker_sent, #TODAY,
                        #mailing_date=datetime.datetime.strptime(date_issued, '%d/%m/%Y').date() if date_issued else None,
                        mailing_date=datetime.datetime.strptime(sticker_sent, '%d/%m/%Y').date() if sticker_sent else None,
                        sticker_printing_batch=None,
                        sticker_printing_response=None,
                    )
#                approval_history.stickers.add(sticker.id)

                #auth_user_moorings = self.df_authuser[(self.df_authuser['pers_no_u']==pers_no_u)]
                #auth_user_moorings = self.df_authuser[(self.df_authuser['pers_no_u']==pers_no_u) & (self.df_authuser['vessel_rego']==rego_no)].drop_duplicates(subset=['mooring_no'])
                #auth_user_moorings = self.df_authuser[(self.df_authuser['vessel_rego']==rego_no)]
                auth_user_moorings = self.df_authuser[(self.df_authuser['vessel_rego']==rego_no)].drop_duplicates(subset=['mooring_no','vessel_rego'])
                #for idx, auth_user in tqdm(self.df_authuser.iterrows(), total=self.df_authuser.shape[0]):
                for idx, auth_user in auth_user_moorings.iterrows():
                    mooring = Mooring.objects.filter(name=auth_user.mooring_no)
                    moa = MooringOnApproval.objects.create(
                        approval=approval,
                        mooring=mooring[0],
                        sticker=sticker,
                        site_licensee=False, #site_licensee,
                        #end_date=expiry_date
                    )

                #approval.generate_doc()

            except Exception as e:
                errors.append(str(e))
                import ipdb; ipdb.set_trace()
                pass
                #raise Exception(str(e))

        print(f'vessel_not_found: {vessel_not_found}')
        print(f'vessel_not_found: {len(vessel_not_found)}')
        print(f'aup_created: {len(aup_created)}')
        print(f'au_stickers: au_stickers, {len(au_stickers)}')
        print(f'no_au_stickers: no_au_stickers, {len(no_au_stickers)}')

    def create_waiting_list(self):
        expiry_date = EXPIRY_DATE
        #start_date = START_DATE
        #date_applied = DATE_APPLIED

        errors = []
        vessel_not_found = []
        user_not_found = []
        wl_created = []

        vessel_type = 'other'

        for index, row in tqdm(self.df_wl.iterrows(), total=self.df_wl.shape[0]):
            try:
                #if row.mooring_no == 'TB999':
                #    continue

                #if row.pers_no == '212841':
                #if row.pers_no == '212397':
                #if row.pers_no == '212425':
#                if row.pers_no == '213766':
#                    import ipdb; ipdb.set_trace()
#                else:
#                    continue

                pers_no = row['pers_no']
                mooring_bay = MooringBay.objects.get(code=row['bay'])

                email = self.df_user[(self.df_user['pers_no']==pers_no) & (self.df_user['email']!='')].iloc[0]['email'].strip()
                first_name = row.first_name.lower().title().strip()
                last_name = row.last_name.lower().title().strip()
                try:
                    user = EmailUser.objects.get(email=email.lower())
#                    user.first_name = first_name
#                    user.last_name = last_name
#                    user.save()
                except Exception as e:
                    user = EmailUser.objects.get(first_name=first_name, last_name=last_name) 

                rego_no = row['vessel_rego']
                try:
                    vessel = Vessel.objects.get(rego_no=rego_no)
                except Exception as e:
                    vessel_not_found.append(f'{pers_no} - {email}: {rego_no}')
                    continue

                vessel_ownership = vessel.vesselownership_set.all()[0]
                vessel_details = vessel.vesseldetails_set.all()[0]

                start_date = parse(row.date_applied).replace(tzinfo=timezone.utc)
                proposal=WaitingListApplication.objects.create(
                    #proposal_type_id=1,
                    proposal_type_id=ProposalType.objects.get(code='new').id, # new application
                    submitter=user.id,
                    lodgement_date=start_date, #TODAY,
                    migrated=True,
                    vessel_details=vessel_details,
                    vessel_ownership=vessel_ownership,
                    rego_no=rego_no,
                    vessel_type=vessel_type,

                    vessel_name=row['vessel_name'],
                    vessel_length=vessel_details.vessel_length,
                    vessel_draft=vessel_details.vessel_draft,
                    vessel_beam=vessel_details.vessel_beam,
                    vessel_weight=vessel_details.vessel_weight,
                    berth_mooring=vessel_details.berth_mooring,

                    preferred_bay=mooring_bay,
                    percentage=vessel_ownership.percentage,
                    individual_owner=True,
                    proposed_issuance_approval={},
                    processing_status='approved',
                    customer_status='approved',
                )

                try:
                    user_row = self.df_user[self.df_user['pers_no']==row.pers_no].squeeze() # as Pandas Series
                except Exception as e:
                    import ipdb; ipdb.set_trace()
                self.create_proposal_applicant(proposal, user, user_row)

                ua=ProposalUserAction.objects.create(
                    proposal=proposal,
                    who=user.id,
                    what='Waiting List - Migrated Application',
                )

#                try:
#                    start_date = parse(row.date_applied).replace(tzinfo=timezone.utc) if row.date_applied else datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
#                except:
#                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d').replace(tzinfo=timezone.utc)

                approval = WaitingListAllocation.objects.create(
                    status=Approval.APPROVAL_STATUS_CURRENT,
                    internal_status=Approval.INTERNAL_STATUS_WAITING,
                    current_proposal=proposal,
                    issue_date = start_date, #TODAY,
                    #start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').date(),
                    start_date = start_date,
                    expiry_date = expiry_date,
                    submitter=user.id,
                    migrated=True,
                    wla_order=row['bay_pos_no'],
                    #wla_queue_date=TODAY + datetime.timedelta(seconds=int(row['bay_pos_no'])),
                    wla_queue_date=start_date + datetime.timedelta(seconds=int(row['bay_pos_no'])),
                )
                #print(f'wla_order: {position_no}')
                wl_created.append(approval.id)

                aua=ApprovalUserAction.objects.create(
                    approval=approval,
                    who=user.id,
                    what='Waiting List Allocation - Migrated Application',
                )

                proposal.approval = approval
                proposal.save()

#                try:
#                    start_date = parse(row.date_applied).date() if row.date_applied else datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').astimezone(datetime.timezone.utc)
#                except:
#                    start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d').astimezone(datetime.timezone.utc)

#                approval_history = ApprovalHistory.objects.create(
#                    reason='new',
#                    approval=approval,
#                    vessel_ownership = vessel_ownership,
#                    proposal = proposal,
#                    start_date = start_date,
#                )

                #approval.generate_doc()

            except Exception as e:
                errors.append(str(e))
                import ipdb; ipdb.set_trace()
                pass
                #raise Exception(str(e))

        print(f'vessel_not_found: {vessel_not_found}')
        print(f'vessel_not_found: {len(vessel_not_found)}')
        print(f'user_not_found: {user_not_found}')
        print(f'user_not_found: {len(user_not_found)}')
        print(f'wl_created: {len(wl_created)}')

    def create_dcv(self):
        expiry_date = EXPIRY_DATE
        start_date = START_DATE
        date_applied = DATE_APPLIED
        fee_season = FeeSeason.objects.filter(application_type__code='dcvp', name=FEE_SEASON)[0]

        errors = []
        vessel_not_found = []
        user_not_found = []
        dcv_created = []

        #vessel_type = 'other'

        for index, row in tqdm(self.df_dcv.iterrows(), total=self.df_dcv.shape[0]):
            try:
                #import ipdb; ipdb.set_trace()
                if row.pers_no == '201043':
                    import ipdb; ipdb.set_trace()

                pers_no = row['pers_no']
                email = row['email']
                org_name = row['company']

                #email = self.df_user[(self.df_user['pers_no']==pers_no) & (self.df_user['email']!='')].iloc[0]['email'].strip()
                first_name = row.first_name.lower().title().strip()
                last_name = row.last_name.lower().title().strip()
                try:
                    user = EmailUser.objects.get(email=email.lower())
                    user.first_name = first_name
                    user.last_name = last_name
                    user.save()
                except Exception as e:
                    user = EmailUser.objects.get(first_name=first_name, last_name=last_name) 

                try:
                    dcv_organisation = DcvOrganisation.objects.get(name=org_name)
                except ObjectDoesNotExist:
                    dcv_organisation = DcvOrganisation.objects.create(name=org_name)


                try:
                    vessels_dcv = self.vessels_dcv[pers_no]
                except Exception as e:
                    vessel_not_found.append(pers_no)
                    continue

                for rego_no, sticker_no in vessels_dcv:
                    try:
                        dcv_vessel = DcvVessel.objects.get(rego_no=rego_no)
                    except ObjectDoesNotExist:
                        vessel_details = VesselDetails.objects.get(vessel__rego_no=rego_no)
                        dcv_vessel = DcvVessel.objects.create(
                            rego_no = rego_no,
                            vessel_name = vessel_details.vessel_name,
                            #dcv_organisation = dcv_organisation,
                        )
                        dcv_vessel.dcv_organisations.add(dcv_organisation)

                    dcv_permit = DcvPermit.objects.create(
                        submitter = user.id,
                        lodgement_datetime = datetime.datetime.now(datetime.timezone.utc),
                        fee_season = fee_season,
                        start_date = start_date,
                        end_date = expiry_date,
                        dcv_vessel = dcv_vessel,
                        dcv_organisation =dcv_organisation,
                        migrated = True
                    )

#                    approval_history = ApprovalHistory.objects.create(
#                        reason='new',
#                        approval=approval,
#                        vessel_ownership = vessel_ownership,
#                        proposal = proposal,
#                        start_date = start_date,
#                    )

                    #import ipdb; ipdb.set_trace()
                    if sticker_no:
                        sticker = Sticker.objects.create(
                            number=sticker_no,
                            status=Sticker.STICKER_STATUS_CURRENT, # 'current'
                            dcv_permit=dcv_permit,
                            #vessel_ownership=vessel_ownership,
                            #printing_date=TODAY,
                            #mailing_date=datetime.datetime.strptime(date_issued, '%d/%m/%Y').date() if date_issued else None,
                            mailing_date=TODAY,
                            #sticker_printing_batch=None,
                            #sticker_printing_response=None,
                        )

                    #import ipdb; ipdb.set_trace()
                    #dcv_permit.generate_dcv_permit_doc()
                    dcv_created.append(dcv_permit.id)



            except Exception as e:
                errors.append(str(e))
                import ipdb; ipdb.set_trace()
                pass
                #raise Exception(str(e))

        print(f'vessel_not_found: {vessel_not_found}')
        print(f'vessel_not_found: {len(vessel_not_found)}')
        print(f'user_not_found: {user_not_found}')
        print(f'user_not_found: {len(user_not_found)}')
        print(f'dcv_created: {len(dcv_created)}')


    def _create_single_vessel(self, user, rego_no, ves_name, ves_type, length, draft, beam, weight, pct_interest, berth_mooring=''):

        def try_except(value):
            try:
                val = float(value)
            except Exception:
                val = 0.00
            return val

        # check if vessel object exists
        try:
            vessel = Vessel.objects.get(rego_no=rego_no)
        except ObjectDoesNotExist:
            vessel = Vessel.objects.create(rego_no=rego_no)

        try:
            owner = Owner.objects.get(emailuser=user)
        except ObjectDoesNotExist:
            owner = Owner.objects.create(emailuser=user)

        try:
            vessel_ownership = VesselOwnership.objects.get(owner=owner, vessel=vessel)
        except ObjectDoesNotExist:
            pct_interest = int(round(float(try_except(pct_interest)),0))
            if pct_interest < 25:
                pct_interest = 100
            vessel_ownership = VesselOwnership.objects.create(owner=owner, vessel=vessel, percentage=pct_interest)

        try:
            vessel_details = VesselDetails.objects.get(vessel=vessel)
        except MultipleObjectsReturned:
            vessel_details = VesselDetails.objects.filter(vessel=vessel)[0]
        except:
            vessel_details = VesselDetails.objects.create(
                vessel=vessel,
                vessel_type=ves_type,
                vessel_name=ves_name,
                vessel_length=try_except(length),
                vessel_draft=try_except(draft),
                vessel_weight=try_except(weight),
                vessel_beam=try_except(beam),
                berth_mooring=''
            )

        return vessel, vessel_details, vessel_ownership


    def create_annual_admissions(self):
        """
            mlr=MooringLicenceReader('PersonDets20221222-125823.txt', 'MooringDets20221222-125546.txt', 'VesselDets20221222-125823.txt', 'UserDets20221222-130353.txt', 'ApplicationDets20221222-125157.txt','annual_admissions_booking_report_20230125084027.csv')
            mlr.create_users()
            mlr.create_vessels()
            mlr.create_annual_admissions()
        """
        expiry_date = EXPIRY_DATE
        start_date = START_DATE
        date_applied = DATE_APPLIED
        fee_season = FeeSeason.objects.filter(application_type__code='aaa', name=FEE_SEASON)[0]

        errors = []
        vessel_not_found = []
        vessel_details_not_found = []
        user_not_found = []
        rego_aa_created = []
        total_aa_created = []

        vessel_type = 'other'

        for index, row in tqdm(self.df_aa.iterrows(), total=self.df_aa.shape[0]):
            try:
                #if row.rego_no == '666':
                #    import ipdb; ipdb.set_trace()

                rego_no = row['rego_no']
                email = row['email']
                sticker_no = row['sticker_no']
                date_created = row['date_created']
                vessel_name = row['vessel_name']
                vessel_length = row['vessel_length']

                #email = self.df_user[(self.df_user['pers_no']==pers_no) & (self.df_user['email']!='')].iloc[0]['email'].strip()
                first_name = row.first_name.lower().title().strip()
                last_name = row.last_name.lower().title().strip()
                try:
                    user = EmailUser.objects.get(email=email.lower().strip())
#                    user.first_name = first_name
#                    user.last_name = last_name
#                    user.save()
                except Exception as e:
                    user = EmailUser.objects.get(first_name=first_name, last_name=last_name) 

#['id', 'date_created', 'first_name', 'last_name', 'address', 'suburb',
#       'state', 'postcode', 'sticker_no', 'rego_no', 'email', 'mobile_number',
#       'work_number', 'vessel_name', 'vessel_length', 'year', 'status',
#       'booking_period', 'country']

                vessel_dict = self.vessels_dict.get(rego_no)
                if vessel_dict is None:
                    vessel, vessel_details, vessel_ownership = self._create_single_vessel(
                        user.id, 
                        rego_no, 
                        ves_name=vessel_name, # use vessel_name from AA Moorings Spreasheet
                        ves_type=vessel_type,
                        length=vessel_length, # use vessel_length from AA Moorings Spreasheet
                        draft=Decimal(0.0), 
                        beam=Decimal(0.0), 
                        weight=Decimal(0.0), 
                        pct_interest=100, 
                    )
                    rego_aa_created.append(rego_no)
                else:
                    try:
                        vessel_details = VesselDetails.objects.get(vessel__rego_no=rego_no)
                        #vessel_ownership = VesselOwnership.objects.get(vessel__rego_no=rego_no)
                        owner = Owner.objects.get(emailuser=user.id)
                        vessel_ownership = VesselOwnership.objects.get(owner=owner, vessel__rego_no=rego_no)
                    except Exception as e:
                        vessel, vessel_details, vessel_ownership = self._create_single_vessel(
                            user.id, 
                            rego_no, 
                            ves_name=vessel_name, # use vessel_name from AA Moorings Spreasheet
                            ves_type=vessel_type,
                            length=vessel_length, # use vessel_length from AA Moorings Spreasheet
                            draft=Decimal(0.0), 
                            beam=Decimal(0.0), 
                            weight=Decimal(0.0), 
                            pct_interest=100, 
                        )

                        #vessel_details_not_found.append((user.email, rego_no))
                        #continue

                #if user.email == 'kdeluca@iinet.net.au':
                #    import ipdb; ipdb.set_trace()

                # update length from AA Spreadsheet file
                vessel_details.vessel_name=vessel_name
                vessel_details.vessel_length=Decimal(vessel_length) if vessel_length else Decimal(0)
                vessel_details.save()

                total_aa_created.append(rego_no)

                proposal=AnnualAdmissionApplication.objects.create(
                    #proposal_type_id=1, # new application
                    proposal_type_id=ProposalType.objects.get(code='new').id, # new application
                    submitter=user.id,
                    lodgement_date=TODAY,
                    migrated=True,
                    vessel_details=vessel_details,
                    vessel_ownership=vessel_ownership,
                    rego_no=rego_no,
                    vessel_type=vessel_details.vessel_type,
                    vessel_name=vessel_details.vessel_name,
                    vessel_length=vessel_details.vessel_length,
                    vessel_draft=vessel_details.vessel_draft,
                    vessel_weight=vessel_details.vessel_weight,
                    percentage=vessel_ownership.percentage,
                    berth_mooring='',
                    individual_owner=True,
                    processing_status='approved',
                    customer_status='approved',
                    proposed_issuance_approval={
                        "start_date": start_date.strftime('%d/%m/%Y'),
                        "expiry_date": expiry_date.strftime('%d/%m/%Y'),
                        "details": None,
                        "cc_email": None,
                    },
                )

                #import ipdb; ipdb.set_trace()
                self.create_proposal_applicant_aa(proposal, user, row)

                ua=ProposalUserAction.objects.create(
                    proposal=proposal,
                    who=user.id,
                    what='Annual Admission - Migrated Application',
                )

                #approval = WaitingListAllocation.objects.create(
                approval = AnnualAdmissionPermit.objects.create(
                    status='current',
                    #internal_status=None,
                    current_proposal=proposal,
                    issue_date = TODAY,
                    #start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').date(),
                    start_date = start_date,
                    expiry_date = expiry_date,
                    submitter=user.id,
                    migrated=True,
                    export_to_mooring_booking=True,
                )
                #print(f'wla_order: {position_no}')

                aua=ApprovalUserAction.objects.create(
                    approval=approval,
                    who=user.id,
                    what='Annual Admission - Migrated Application',
                )

                proposal.approval = approval
                proposal.save()
                #proposal.allocated_mooring.mooring_licence = approval
                #proposal.allocated_mooring.save()

#                approval_history = ApprovalHistory.objects.create(
#                    reason='new',
#                    approval=approval,
#                    vessel_ownership = vessel_ownership,
#                    proposal = proposal,
#                    #start_date = datetime.datetime.strptime(date_applied, '%Y-%m-%d %H:%M:%S').astimezone(datetime.timezone.utc)
#                    start_date = start_date,
#                )

                if sticker_no:
                    sticker = Sticker.objects.create(
                        number=sticker_no,
                        status=Sticker.STICKER_STATUS_CURRENT, # 'current'
                        approval=approval,
                        proposal_initiated=proposal,
                        vessel_ownership=vessel_ownership,
                        printing_date=datetime.datetime.strptime(date_created.split(' ')[0], '%d/%m/%Y').date() if date_created else None,
                        mailing_date=datetime.datetime.strptime(date_created.split(' ')[0], '%d/%m/%Y').date() if date_created else None,
                        sticker_printing_batch=None,
                        sticker_printing_response=None,
                    )
#                    approval_history.stickers.add(sticker.id)

                #approval.generate_doc()

            except Exception as e:
                errors.append(str(e))
                import ipdb; ipdb.set_trace()
                pass
                #raise Exception(str(e))

        print(f'rego_aa_created: {len(rego_aa_created)}')
        print(f'total_aa_created: {len(total_aa_created)}')
        print(f'Vessel Details Not Found: {vessel_details_not_found}')
        print(f'Vessel Details Not Found: {len(vessel_details_not_found)}')


    def _migrate_approval(self, data, submitter, applicant=None, proxy_applicant=None):
        from disturbance.components.approvals.models import Approval
        #import ipdb; ipdb.set_trace()
        try:
            site_number = int(data.permit_number) if data.permit_number else int(data.licensed_site)
        except Exception as e:
            import ipdb; ipdb.set_trace()
            print('ERROR: There is no site_number - Must provide a site number in migration spreadsheeet. {e}')

        try:
            expiry_date = data['expiry_date'] if data['expiry_date'] else datetime.date.today()
            start_date = data['start_date'] if data['start_date'] else datetime.date.today()
            issue_date = data['issue_date'] if data['issue_date'] else start_date
            site_status = 'not_to_be_reissued' if data['status'].lower().strip() == 'not to be reissued' else data['status'].lower().strip()

        except Exception as e:
            import ipdb; ipdb.set_trace()
            print(e)

        try:

            #import ipdb; ipdb.set_trace()
            if applicant:
                proposal, p_created = Proposal.objects.get_or_create(
                                application_type=self.application_type,
                                activity='Apiary',
                                submitter=submitter,
                                applicant=applicant,
                                schema=self.proposal_type.schema,
                            )
                approval, approval_created = Approval.objects.update_or_create(
                                applicant=applicant,
                                status=Approval.STATUS_CURRENT,
                                apiary_approval=True,
                                defaults = {
                                    'issue_date':issue_date,
                                    'expiry_date':expiry_date,
                                    'start_date':start_date,
                                    #'submitter':submitter,
                                    'current_proposal':proposal,
                                    }
                            )
            else:
                import ipdb; ipdb.set_trace()

            #if 'PM' not in proposal.lodgement_number:
            #    proposal.lodgement_number = proposal.lodgement_number.replace('P', 'PM') # Application Migrated
            proposal.approval= approval
            proposal.processing_status='approved'
            proposal.customer_status='approved'
            proposal.migrated=True
            proposal.proposed_issuance_approval = {
                    'start_date': start_date.strftime('%d-%m-%Y'),
                    'expiry_date': expiry_date.strftime('%d-%m-%Y'),
                    'details': 'Migrated',
                    'cc_email': 'Migrated',
            }

            approval.migrated=True

            # create invoice for payment of zero dollars
#            order = create_invoice(proposal)
#            invoice = Invoice.objects.get(order_number=order.number) 
#            proposal.fee_invoice_references = [invoice.reference]
            proposal.fee_invoice_references = [10000001]

            proposal.save()
            approval.save()

            # create apiary sites and intermediate table entries
            #geometry = GEOSGeometry('POINT(' + str(data['latitude']) + ' ' + str(data['longitude']) + ')', srid=4326)
            geometry = GEOSGeometry('POINT(' + str(data['latitude']) + ' ' + str(data['longitude']) + ')', srid=4326)
            #import ipdb; ipdb.set_trace()
            apiary_site = ApiarySite.objects.create(
                    id=site_number,
                    is_vacant=True if site_status=='vacant' else False
                    )
            site_category = get_category(geometry)
            intermediary_approval_site = ApiarySiteOnApproval.objects.create(
                                            #id=site_number,
                                            apiary_site=apiary_site,
                                            approval=approval,
                                            wkb_geometry=geometry,
                                            site_category = site_category,
                                            licensed_site=True if data['licensed_site'] else False,
                                            batch_no=data['batch_no'],
                                            approval_cpc_date=data['approval_cpc_date'] if data.approval_cpc_date else None,
                                            approval_minister_date=data['approval_minister_date'] if data.approval_minister_date else None,
                                            map_ref=data['map_ref'],
                                            forest_block=data['forest_block'],
                                            cog=data['cog'],
                                            roadtrack=data['roadtrack'],
                                            zone=data['zone'],
                                            catchment=data['catchment'],
                                            #dra_permit=data['dra_permit'],
                                            site_status=site_status,
                                            )
            #import ipdb; ipdb.set_trace()
            pa, pa_created = ProposalApiary.objects.get_or_create(proposal=proposal)

            intermediary_proposal_site = ApiarySiteOnProposal.objects.create(
                                            #id=site_number,
                                            apiary_site=apiary_site,
                                            #approval=approval,
                                            proposal_apiary=pa,
                                            wkb_geometry_draft=geometry,
                                            site_category_draft = site_category,
                                            wkb_geometry_processed=geometry,
                                            site_category_processed = site_category,
                                            licensed_site=True if data['licensed_site'] else False,
                                            batch_no=data['batch_no'],
                                            #approval_cpc_date=data['approval_cpc_date'],
                                            #approval_minister_date=data['approval_minister_date'],
                                            approval_cpc_date=data['approval_cpc_date'] if data.approval_cpc_date else None,
                                            approval_minister_date=data['approval_minister_date'] if data.approval_minister_date else None,
                                            map_ref=data['map_ref'],
                                            forest_block=data['forest_block'],
                                            cog=data['cog'],
                                            roadtrack=data['roadtrack'],
                                            zone=data['zone'],
                                            catchment=data['catchment'],
                                            site_status=site_status,
                                            #dra_permit=data['dra_permit'],
                                            )
            #import ipdb; ipdb.set_trace()

            apiary_site.latest_approval_link=intermediary_approval_site
            apiary_site.latest_proposal_link=intermediary_proposal_site
            if site_status == 'vacant':
                apiary_site.approval_link_for_vacant=intermediary_approval_site
                apiary_site.proposal_link_for_vacant=intermediary_proposal_site
            apiary_site.save()


        except Exception as e:
            logger.error('{}'.format(e))
            import ipdb; ipdb.set_trace()
            return None

        return approval

#    def create_licence_pdf(self):
#        approvals_migrated = Approval.objects.filter(migrated=True)
#        print('Total Approvals: {} - {}'.format(approvals_migrated.count(), approvals_migrated))
#        for idx, a in enumerate(approvals_migrated):
#            a.generate_doc()
#            print('{}, Created PDF for Approval {}'.format(idx, a))
#
#    def create_dcv_licence_pdf(self):
#        approvals_migrated = DcvPermit.objects.filter(migrated=True)
#        print('Total DCV Approvals: {} - {}'.format(approvals_migrated.count(), approvals_migrated))
#        for idx, a in enumerate(approvals_migrated):
#            a.generate_dcv_permit_doc()
#            print('{}, Created PDF for DCV Approval {}'.format(idx, a))


    def create_licence_pdfs(self):
        MooringLicenceReader.create_pdf_ml()
        MooringLicenceReader.create_pdf_aup()
        MooringLicenceReader.create_pdf_wl()
        MooringLicenceReader.create_pdf_aa()
        MooringLicenceReader.create_pdf_dcv()
    
    @classmethod
    def create_pdf_ml(self):
        """ MooringLicenceReader.create_pdf_ml()
        """
        self._create_pdf_licence(MooringLicence.objects.filter(migrated=True))

    @classmethod
    def create_pdf_aup(self):
        """ MooringLicenceReader.create_pdf_aup()
        """
        self._create_pdf_licence(AuthorisedUserPermit.objects.filter(migrated=True))

    @classmethod
    def create_pdf_wl(self):
        """ MooringLicenceReader.create_pdf_wl()
        """
        self._create_pdf_licence(WaitingListAllocation.objects.filter(migrated=True))

    @classmethod
    def create_pdf_aa(self):
        """ MooringLicenceReader.create_pdf_aa()
        """
        self._create_pdf_licence(AnnualAdmissionPermit.objects.filter(migrated=True))

    @classmethod
    def create_pdf_dcv(self):
        """ MooringLicenceReader.create_pdf_dcv()
        """
        self._create_pdf_licence(DcvPermit.objects.filter(migrated=True))

    @staticmethod
    def _create_pdf_licence(approvals_migrated):
        """ MooringLicenceReader._create_pdf_licence(MooringLicence.objects.filter(migrated=True), current_proposal__processing_status=Proposal.PROCESSING_STATUS_APPROVED)
        """
        #approvals_migrated = MooringLicence.objects.filter(migrated=True)
        permit_name = approvals_migrated[0].__class__.__name__
        print(f'Total {permit_name}: {approvals_migrated.count()} - {approvals_migrated}')
        if isinstance(approvals_migrated[0], DcvPermit):
            approvals = approvals_migrated.filter(migrated=True)
        else:
            approvals = approvals_migrated.filter(migrated=True, current_proposal__processing_status=Proposal.PROCESSING_STATUS_APPROVED)

        for idx, a in enumerate(approvals):
            try:
                if isinstance(a, DcvPermit) and len(a.dcv_permit_documents.all())==0:
                    a.generate_dcv_permit_doc()
                elif not hasattr(a, 'licence_document') or a.licence_document is None: 
                    a.generate_doc()
                print(f'{idx}, Created PDF for {permit_name}: {a}')
            except Exception as e:
                logger.error(e)


def create_invoice(proposal, payment_method='other'):
        """
        This will create and invoice and order from a basket bypassing the session
        and payment bpoint code constraints.
        """
        from ledger.checkout.utils import createCustomBasket
        from ledger.payments.invoice.utils import CreateInvoiceBasket
        from ledger.accounts.models import EmailUser

        now = timezone.now().date()
        line_items = [
            {'ledger_description': 'Migration Licence Charge Waiver - {} - {}'.format(now, proposal.lodgement_number),
             'oracle_code': 'N/A', #proposal.application_type.oracle_code_application,
             'price_incl_tax':  Decimal(0.0),
             'price_excl_tax':  Decimal(0.0),
             'quantity': 1,
            }
        ]

        user = EmailUser.objects.get(email__icontains='das@dbca.wa.gov.au')
        invoice_text = 'Migrated Permit/Licence Invoice'

        basket  = createCustomBasket(line_items, user, settings.PAYMENT_SYSTEM_ID)
        order = CreateInvoiceBasket(payment_method=payment_method, system=settings.PAYMENT_SYSTEM_PREFIX).create_invoice_and_order(basket, 0, None, None, user=user, invoice_text=invoice_text)

        return order
    

def write_proposal_applicants(filename, path='/tmp/'):
    qs=ProposalApplicant.objects.all().values('email','first_name', 'last_name', 'dob', 'phone_number', 'mobile_number').distinct('email')

    with open(path+filename, 'w') as file:
        write = csv.writer(file)
        write.writerow(['email','first_name','last_name','dob', 'phone_number', 'mobile_number'])
        for i in qs:
            write.writerows([i.values()])



