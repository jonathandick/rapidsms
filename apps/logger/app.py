#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4

import rapidsms

import models
from models import OutgoingMessage, IncomingMessage

class App(rapidsms.app.App):
    
    def parse(self, message):
        # make and save messages on their way in 
        persistent_msg = IncomingMessage.objects.create(identity=message.connection.identity, \
                                                        text=message.text, \
                                                        backend=message.connection.backend.slug)
        message.persistent_msg = persistent_msg
        self.debug(persistent_msg)
    
    def outgoing(self, message):
        # make and save messages on their way out and 
        # cast connection as string so pysqlite doesnt complain
        msg = OutgoingMessage.objects.create(identity=message.connection.identity, text=message.text, 
                                             backend=message.connection.backend.slug)
        self.debug(msg)
        # inject this id into the message object.
        message.logger_id = msg.id;