"""Access tokens and authorization codes become random strings, of which only
the SHA-256 is kept, in place of signed JWTs."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('oauth2', '0009_oauth2application_is_default'),
    ]

    operations = [
        # Codes given out before are JWTs, whose IDs are not hashes of them: they
        # cannot be exchanged now, and expire within minutes anyway
        migrations.RemoveIndex(model_name='oauth2authorizationcode', name='oauth2_code_jti_idx'),
        migrations.RenameField(model_name='oauth2authorizationcode', old_name='jti', new_name='code_hash'),
        # Tokens given out before have no hash here, and stop working
        migrations.AddField(
            model_name='oauth2authorization',
            name='token_hash',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='oauth2authorization',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
