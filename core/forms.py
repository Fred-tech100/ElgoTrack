from django import forms


class StyledFormMixin:
    """Applies the design-system CSS classes to every widget."""

    def _style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs.setdefault("class", "h-4 w-4 rounded border-slate-300 text-brand-700 focus:ring-brand-600")
            elif isinstance(widget, forms.RadioSelect):
                continue
            elif isinstance(widget, forms.FileInput):
                widget.attrs.setdefault("class", "input-file")
            elif isinstance(widget, forms.PasswordInput):
                widget.attrs.setdefault("class", "input pr-11")
            else:
                widget.attrs.setdefault("class", "input")
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 3)


class StyledForm(StyledFormMixin, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class BusinessForm(StyledFormMixin, forms.Form):
    """Plain form whose model choice fields are restricted to one business."""

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        _scope_choices(self, business)
        self._style_fields()


class BusinessModelForm(StyledFormMixin, forms.ModelForm):
    """ModelForm for tenant-owned models: scopes choices and sets ``business``."""

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        if business is not None and not self.instance.business_id:
            self.instance.business = business
        _scope_choices(self, business)
        self._style_fields()

    def _get_validation_exclusions(self):
        exclude = super()._get_validation_exclusions()
        exclude.discard("business")  # so per-business unique constraints are validated
        return exclude


def _scope_choices(form, business):
    if business is None:
        return
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField):
            model = field.queryset.model
            if any(f.name == "business" for f in model._meta.fields):
                field.queryset = field.queryset.filter(business=business)
