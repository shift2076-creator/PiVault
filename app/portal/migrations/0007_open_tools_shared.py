from django.db import migrations

OPEN = ('books', 'games', 'documents', 'wiki')


def share_open_tools(apps, schema_editor):
    """These tools have no per-user accounts, so every user has them."""
    User = apps.get_model('portal', 'User')
    for user in User.objects.all():
        access = dict(user.service_access or {})
        if any(access.get(key) != 'shared' for key in OPEN):
            access.update({key: 'shared' for key in OPEN})
            user.service_access = access
            user.save(update_fields=['service_access'])


class Migration(migrations.Migration):
    dependencies = [('portal', '0006_retire_inventory_pantry')]
    operations = [migrations.RunPython(share_open_tools, migrations.RunPython.noop)]
