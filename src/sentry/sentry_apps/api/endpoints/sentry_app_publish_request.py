from collections.abc import Iterable

from rest_framework.request import Request
from rest_framework.response import Response

from sentry import options
from sentry.api.api_owners import ApiOwner
from sentry.api.api_publish_status import ApiPublishStatus
from sentry.api.base import control_silo_endpoint
from sentry.constants import SentryAppStatus
from sentry.models.organizationmapping import OrganizationMapping
from sentry.sentry_apps.api.bases.sentryapps import COMPONENT_TYPES, SentryAppBaseEndpoint
from sentry.sentry_apps.logic import SentryAppUpdater
from sentry.sentry_apps.models.sentry_app_avatar import SentryAppAvatar, SentryAppAvatarTypes
from sentry.users.models.user import User
from sentry.users.services.user.model import RpcUser
from sentry.utils import email


@control_silo_endpoint
class SentryAppPublishRequestEndpoint(SentryAppBaseEndpoint):
    owner = ApiOwner.INTEGRATIONS
    publish_status = {
        "POST": ApiPublishStatus.PRIVATE,
    }

    def has_ui_component(self, sentry_app):
        """Determine if the sentry app supports issue linking or stack trace linking."""
        elements = (sentry_app.schema or {}).get("elements", [])
        return any(element.get("type") in COMPONENT_TYPES for element in elements)

    def post(self, request: Request, sentry_app) -> Response:
        # check status of app to make sure it is unpublished
        if sentry_app.is_published:
            return Response({"detail": "Cannot publish already published integration."}, status=400)

        if sentry_app.is_internal:
            return Response({"detail": "Cannot publish internal integration."}, status=400)

        if sentry_app.is_publish_request_inprogress:
            return Response({"detail": "Publish request in progress."}, status=400)

        if not SentryAppAvatar.objects.filter(
            sentry_app=sentry_app, color=True, avatar_type=SentryAppAvatarTypes.UPLOAD.value
        ).exists():
            return Response({"detail": "Must upload a logo for the integration."}, status=400)

        if (
            self.has_ui_component(sentry_app)
            and not SentryAppAvatar.objects.filter(
                sentry_app=sentry_app,
                color=False,
                avatar_type=SentryAppAvatarTypes.UPLOAD.value,
            ).exists()
        ):
            return Response(
                {"detail": "Must upload an icon for issue and stack trace linking integrations."},
                status=400,
            )

        assert isinstance(
            request.user, (User, RpcUser)
        ), "User must be authenticated to update a Sentry App"
        SentryAppUpdater(
            sentry_app=sentry_app,
            status=SentryAppStatus.PUBLISH_REQUEST_INPROGRESS_STR,
        ).run(user=request.user)

        org_mapping = OrganizationMapping.objects.filter(
            organization_id=sentry_app.owner_id
        ).first()
        org_slug = "<unknown>" if org_mapping is None else org_mapping.slug
        message = f"User {request.user.email} of organization {org_slug} wants to publish {sentry_app.slug}\n"

        questionnaire: Iterable[dict[str, str]] = request.data.get("questionnaire", [])
        for question_pair in questionnaire:
            message += "\n\n>{}\n{}".format(
                question_pair.get("question", ""), question_pair.get("answer", "")
            )

        subject = "Sentry Integration Publication Request from %s" % org_slug

        email.send_mail(
            subject,
            message,
            options.get("mail.from"),
            ["partners@sentry.io"],
            reply_to=[request.user.email],
        )

        return Response(status=201)
