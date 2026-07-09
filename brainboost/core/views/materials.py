from .common import *


@login_required
def material_download(request, material_id):
    _ensure_profile_for_user(request.user)
    material = get_object_or_404(
        LearningMaterial.objects.select_related("student__user", "uploaded_by__user"),
        pk=material_id,
    )
    if not _can_access_material(request.user, material):
        messages.error(request, "Du darfst dieses Material nicht herunterladen.")
        return redirect("dashboard")

    try:
        material.file.open("rb")
    except FileNotFoundError:
        messages.error(request, "Die angeforderte Datei wurde nicht gefunden.")
        return redirect("dashboard")
    filename = Path(material.file.name).name
    return FileResponse(material.file, as_attachment=True, filename=filename)


@login_required
def material_upload(request, kind: str):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    if kind not in (LearningMaterial.Kind.TASK, LearningMaterial.Kind.SOLUTION):
        return redirect("dashboard")

    allowed_students = _assigned_students_qs(request.user.tutor_profile)
    heading = "Aufgabe hochladen" if kind == LearningMaterial.Kind.TASK else "Ergebnisse hochladen"

    if request.method == "POST":
        form = LearningMaterialForm(
            data=request.POST,
            files=request.FILES,
            allowed_students=allowed_students,
            kind=kind,
            tutor_profile=request.user.tutor_profile,
        )
        if form.is_valid():
            material = form.save(commit=False)
            material.kind = kind
            material.uploaded_by = request.user.tutor_profile
            material.save()
            notify_material_uploaded(request, material)
            return redirect("dashboard")
    else:
        form = LearningMaterialForm(
            allowed_students=allowed_students,
            kind=kind,
            tutor_profile=request.user.tutor_profile,
        )

    return render(
        request,
        "material_upload.html",
        {"form": form, "heading": heading},
    )


@login_required
def tutor_solution_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    tutor_profile = request.user.tutor_profile
    assigned_students = _assigned_students_qs(tutor_profile)
    solutions = (
        LearningMaterial.objects.filter(kind=LearningMaterial.Kind.SOLUTION)
        .filter(Q(student__in=assigned_students) | Q(uploaded_by=tutor_profile))
        .select_related("student__user", "uploaded_by__user", "related_task")
        .distinct()
    )

    return render(
        request,
        "tutor_solution_list.html",
        {"solutions": solutions},
    )


@login_required
def material_delete(request, material_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("tutor_solution_list")

    material = get_object_or_404(
        LearningMaterial.objects.select_related("student__user", "uploaded_by__user"),
        pk=material_id,
    )
    if not can_delete_material(request.user, material):
        messages.error(request, "Du darfst diese Datei nicht löschen.")
        return redirect("tutor_solution_list")

    next_url = request.POST.get("next") or reverse("tutor_solution_list")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("tutor_solution_list")

    material.file.delete(save=False)
    material.delete()
    messages.success(request, "Datei wurde gelöscht.")
    return redirect(next_url)


@login_required
def tutor_template_list(request):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")

    is_admin_tutor = request.user.is_staff or request.user.is_superuser

    if request.method == "POST":
        if not is_admin_tutor:
            return redirect("tutor_template_list")
        form = TutorTemplateForm(request.POST, request.FILES, is_admin_tutor=is_admin_tutor)
        if form.is_valid():
            template = form.save(commit=False)
            template.uploaded_by = request.user.tutor_profile
            template.save()
            messages.success(request, "Vorlage wurde hochgeladen.")
            return redirect("tutor_template_list")
    else:
        form = TutorTemplateForm(is_admin_tutor=is_admin_tutor)

    templates = TutorTemplate.objects.select_related("uploaded_by__user")
    if is_admin_tutor:
        templates = templates.filter(
            Q(visibility=TutorTemplate.Visibility.ADMINS)
            | Q(visibility=TutorTemplate.Visibility.BOTH)
        )
    else:
        templates = templates.filter(
            Q(visibility=TutorTemplate.Visibility.TUTORS)
            | Q(visibility=TutorTemplate.Visibility.BOTH)
        )

    return render(
        request,
        "tutor_template_list.html",
        {
            "templates": templates,
            "form": form,
            "is_admin_tutor": is_admin_tutor,
        },
    )


@login_required
def tutor_template_delete(request, template_id):
    _ensure_profile_for_user(request.user)
    if request.user.role != CustomUser.Roles.TUTOR or not hasattr(request.user, "tutor_profile"):
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("tutor_template_list")

    tutor_profile = request.user.tutor_profile
    template = get_object_or_404(
        TutorTemplate.objects.select_related("uploaded_by__user"),
        pk=template_id,
    )
    if not (_has_admin_access(request.user) or template.uploaded_by_id == tutor_profile.id):
        messages.error(request, "Du darfst diese Vorlage nicht löschen.")
        return redirect("tutor_template_list")

    next_url = request.POST.get("next") or reverse("tutor_template_list")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("tutor_template_list")

    template.file.delete(save=False)
    template.delete()
    messages.success(request, "Vorlage wurde gelöscht.")
    return redirect(next_url)
