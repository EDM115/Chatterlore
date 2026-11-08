from __future__ import annotations

CONTROL_ERROR_STATUS = {
    "embedding_job_not_found": 404,
    "embedding_space_not_found": 404,
    **dict.fromkeys(
        {
            "embedding_job_version_conflict",
            "embedding_job_cancelled",
            "embedding_job_not_ready",
            "embedding_job_activated",
            "embedding_job_failed",
            "embedding_job_lease_lost",
            "embedding_profile_changed",
            "embedding_profile_unavailable",
            "embedding_settings_changed",
            "embedding_coverage_required",
            "embedding_job_busy",
            "embedding_route_changed",
            "embedding_space_conflict",
            "embedding_space_dimension_changed",
            "embedding_migration_required",
            "embedding_adoption_row_changed",
            "embedding_adoption_source_changed",
            "embedding_adoption_target_exists",
            "embedding_adoption_preview_changed",
            "profile_in_use",
            "local_preparation_profile_changed",
            "local_preparation_required",
            "local_artifact_revision_changed",
            "local_model_preparation_required",
        },
        409,
    ),
    **dict.fromkeys(
        {
            "embedding_request_invalid",
            "embedding_command_invalid",
            "embedding_confirmation_required",
            "embedding_profile_invalid",
            "embedding_model_invalid",
            "embedding_batch_limit_invalid",
            "embedding_dimension_invalid",
            "embedding_endpoint_invalid",
            "embedding_target_invalid",
            "embedding_vector_invalid",
            "embedding_provider_failure",
            "embedding_adoption_manifest_invalid",
            "embedding_adoption_provenance_invalid",
            "embedding_adoption_vector_invalid",
            "embedding_adoption_confirmation_required",
            "local_preparation_unsupported",
            "local_download_forbidden",
            "local_download_metadata_unavailable",
            "local_download_too_large",
            "local_preparation_failed",
        },
        422,
    ),
}


def control_error_code(exc: Exception) -> str:
    from chatterlore.control_plane.secrets.service import ProviderSecretConflictError

    if isinstance(exc, ProviderSecretConflictError):
        return "local_preparation_profile_changed"
    candidate = getattr(exc, "code", None) or str(exc).split(" ", 1)[0].split(":", 1)[0]
    return (
        candidate
        if isinstance(candidate, str) and candidate in CONTROL_ERROR_STATUS
        else "embedding_request_invalid"
    )
