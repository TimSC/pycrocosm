from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.contrib.auth.hashers import identify_hasher, make_password


def hash_existing_client_secrets(apps, schema_editor):
    Oauth2Application = apps.get_model('oauth2', 'Oauth2Application')
    for app in Oauth2Application.objects.all():
        try:
            identify_hasher(app.client_secret)
        except ValueError:
            app.client_secret = make_password(app.client_secret)
            app.save(update_fields=['client_secret'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('oauth2', '0005_alter_oauth2application_client_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='Oauth2AuthorizationCode',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('jti', models.CharField(max_length=64, unique=True)),
                ('redirect_uri', models.TextField()),
                ('parent_app', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='oauth2.oauth2application')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='oauth2authorizationcode',
            index=models.Index(fields=['jti'], name='oauth2_code_jti_idx'),
        ),
        migrations.AddIndex(
            model_name='oauth2authorizationcode',
            index=models.Index(fields=['parent_app'], name='oauth2_code_parent_app_idx'),
        ),
        migrations.AddIndex(
            model_name='oauth2authorizationcode',
            index=models.Index(fields=['user'], name='oauth2_code_user_idx'),
        ),
        migrations.RunPython(hash_existing_client_secrets, noop_reverse),
    ]
