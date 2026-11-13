from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('portal', '0007_open_tools_shared')]
    operations = [
        migrations.AddField(
            model_name='invitation',
            name='shared_sealed',
            field=models.TextField(blank=True, default=''),
        ),
    ]
