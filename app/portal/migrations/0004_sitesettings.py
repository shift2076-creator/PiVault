from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0003_user_vault_items_user_vault_user_id_clientrelease_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('offline_mode', models.BooleanField(default=False)),
            ],
        ),
    ]
