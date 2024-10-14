from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from django.views.decorators.csrf import csrf_exempt

# Create your views here.
@permission_classes((IsAuthenticated, ))
def authorize(request):
	return HttpResponse("", content_type='text/plain')
	#return render(request, 'frontpage/index.html', {'db_status': dbStatus})

@csrf_exempt
@api_view(['POST'])
def token(request):
	return HttpResponse("", content_type='text/plain')

