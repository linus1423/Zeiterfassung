from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .models import Reminder


@login_required
def reminder_list(request):
    """Die offenen Hinweise der angemeldeten Person."""
    return render(request, "reminders/list.html", {"reminders": services.open_for(request.user)})


@require_POST
@login_required
def reminder_dismiss(request, reminder_id):
    """Einen Hinweis wegklicken. Er kommt zu diesem Anlass nicht wieder."""
    reminder = get_object_or_404(Reminder, pk=reminder_id, recipient=request.user)
    reminder.resolve()
    messages.success(request, "Hinweis ausgeblendet.")
    return redirect("reminders:list")
