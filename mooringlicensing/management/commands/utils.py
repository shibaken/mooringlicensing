from mooringlicensing.components.main.models import GlobalSettings


def ml_meet_vessel_requirement(mooring_licence, boundary_date):
    min_length_setting = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINUMUM_MOORING_VESSEL_LENGTH)
    min_length = float(min_length_setting.value)

    # Return True when mooring_licence has at least one vessel whoose size is more than 6.4m and not sold or sold after the boundary_date
    for proposal in mooring_licence.proposal_set.all():
        if proposal.vessel_details and proposal.vessel_details.vessel and proposal.vessel_details.vessel.latest_vessel_details and proposal.vessel_details.vessel.latest_vessel_details.vessel_applicable_length >= min_length:
            # Vessel meets the length requirements
            if proposal.vessel_ownership.end_date is None or proposal.vessel_ownership.end_date > boundary_date:
                # At least one vessel has not been sold or sold recently
                return True

    # All the vessels have been sold more than X months ago
    return False