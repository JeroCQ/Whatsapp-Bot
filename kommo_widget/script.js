define(['jquery'], function ($) {
  'use strict';

  return function RailwayAgentWidget() {
    this.callbacks = {
      settings: function () {},
      init: function () { return true; },
      bind_actions: function () { return true; },
      render: function () { return true; },

      onSalesbotDesignerSave: function (_handlerCode, params) {
        var webhookUrl = String((params && params.webhook_url) || '').trim();
        if (!/^https:\/\//i.test(webhookUrl)) {
          throw new Error('The Railway webhook URL must start with https://');
        }

        return JSON.stringify([
          {
            question: [
              {
                handler: 'widget_request',
                params: {
                  url: webhookUrl,
                  data: {
                    message: '{{message_text}}',
                    contact_id: '{{contact.id}}',
                    lead_id: '{{lead.id}}',
                    from: 'railway_ai_agent'
                  }
                }
              }
            ],
            require: []
          }
        ]);
      },

      destroy: function () {},
      onSave: function () { return true; }
    };

    return this;
  };
});
