from app.tools.base import Tool
class GetUserLocationTool(Tool):
    def __init__(self,service):self.service=service
    @property
    def name(self):return "get_user_location"
    @property
    def description(self):return "Returns the requesting user's confirmed city and IANA timezone."
    def parameters(self):return {"type":"object","properties":{},"required":[],"additionalProperties":False}
    def execute(self,**kwargs):
        owner=kwargs.pop("trusted_owner_id",None)
        if kwargs or not isinstance(owner,int) or owner<=0:raise ValueError("trusted owner required")
        item=self.service.get(owner)
        return {"configured":False} if item is None else {"configured":True,"city":item.city,"country":item.country,"timezone":item.timezone,"updated_at":item.updated_at}
